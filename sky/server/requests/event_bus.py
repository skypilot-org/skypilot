"""Event bus for the server."""

import asyncio
import atexit
import collections
import threading
from typing import Callable, Coroutine, DefaultDict, Optional, Set

import zmq
import zmq.asyncio

from sky import sky_logging
from sky.server.requests import requests as requests_lib

_REQUEST_UPDATE_PUB_ENDPOINT = 'ipc:///tmp/skypilot_request_pub'
_REQUEST_UPDATE_SUB_ENDPOINT = 'ipc:///tmp/skypilot_request_sub'

logger = sky_logging.init_logger(__name__)


class EventBus:
    """The centrailzied event bus.

    EventBus must be started in the main process so that other processes
    can publish/subscribe to the event bus.
    """

    def __init__(self, pub_endpoint: str, sub_endpoint: str):
        ctx = zmq.Context()
        self._xsub = ctx.socket(zmq.XSUB)
        # Recv from all the publishers.
        self._xsub.bind(pub_endpoint)
        self._xpub = ctx.socket(zmq.XPUB)
        self._xpub.setsockopt(zmq.XPUB_VERBOSE, 1)
        # Send to all the subscribers.
        self._xpub.bind(sub_endpoint)

    def start(self):
        print('starting event bus')
        self._thread = threading.Thread(target=zmq.proxy,
                                        args=(self._xsub, self._xpub),
                                        daemon=True)
        self._thread.start()


def start():
    event_bus = EventBus(_REQUEST_UPDATE_PUB_ENDPOINT,
                         _REQUEST_UPDATE_SUB_ENDPOINT)
    event_bus.start()


class Publisher:
    """Publisher of request status."""

    def __init__(self, endpoint: str):
        self._ctx = zmq.asyncio.Context.instance()
        self._endpoint = endpoint
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.connect(self._endpoint)

    async def publish(self, request_id: str,
                      status: requests_lib.RequestStatus):
        await self._sock.send_multipart(
            [request_id.encode('utf-8'),
             status.value.encode('utf-8')])

    async def close(self):
        self._sock.close(linger=0)


_publisher_lock = asyncio.Lock()
_publisher: Optional[Publisher] = None


async def _get_publisher():
    global _publisher
    if _publisher is not None:
        return _publisher
    async with _publisher_lock:
        if _publisher is None:
            _publisher = Publisher(_REQUEST_UPDATE_PUB_ENDPOINT)
        return _publisher


async def publish(request_id: str, status: requests_lib.RequestStatus):
    publisher = await _get_publisher()
    print(f'Publishing message for request {request_id} with status {status}')
    await publisher.publish(request_id, status)


class Callback:
    """A wrapper of request status callback that can filter on the status."""

    def __init__(self, least_status: requests_lib.RequestStatus,
                 fn: Callable[[str, requests_lib.RequestStatus], Coroutine]):
        self._least_status = least_status
        self._fn = fn

    def filter(self, status: requests_lib.RequestStatus) -> bool:
        return status > self._least_status

    async def run(self, request_id: str, status: requests_lib.RequestStatus):
        await self._fn(request_id, status)


class Subscriber:
    """Subscriber of request status."""

    def __init__(self, endpoint: str):
        self._ctx = zmq.asyncio.Context.instance()
        self._endpoint = endpoint
        self._callbacks: DefaultDict[
            str, Set[Callback]] = collections.defaultdict(set)
        self._lock = asyncio.Lock()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.connect(self._endpoint)
        self._listener_task = asyncio.create_task(self._listener_loop())

    async def _listener_loop(self):
        print('start listener loop')
        while True:
            # Note that this will yield if there is no message received. We
            # should make sure there is no busy-waiting when modifying this
            # part.
            topic_key, body = await self._sock.recv_multipart()
            request_id = topic_key.decode('utf-8')
            print(f'Received message for request {request_id}')
            try:
                status = requests_lib.RequestStatus(body.decode('utf-8'))
            except ValueError:
                logger.error(f'Invalid status: {body.decode("utf-8")}')
                continue
            async with self._lock:
                if request_id in self._callbacks:
                    callbacks_to_remove = []
                    for cb in self._callbacks[request_id]:
                        if cb.filter(status):
                            asyncio.create_task(cb.run(request_id, status))
                            callbacks_to_remove.append(cb)
                    for cb in callbacks_to_remove:
                        self._callbacks[request_id].remove(cb)
                    if not self._callbacks[request_id]:
                        # No more listeners, unsubscribe this request.
                        self._sock.unsubscribe(request_id.encode('utf-8'))
                        del self._callbacks[request_id]

    async def on_request_update(self, request_id: str, callback: Callback):
        """Register a callback to be called when the request status is updated.

        The time sequence here to avoid race condition relies on
        two assumptions:
        1. Read-after-write consistency of the request DB.
        2. The request status is monotonic.

        Condition #1, happy path, we will not lose event:
        - WithLock{Add the callback}
        - WithLock{Handle the eventA}

        Condition #2:
        - WithLock{Handle the eventA}
          (Boom: the event has been handled here, but the callback has not
           been added)
        - WithLock{Add the callback}
        - Check the request status, because we have read-after-write
          consistency and the request status is monotonic, the status we see
          now must have eventA included. Republishing it can guarantee the
          eventual consistency.
        """
        async with self._lock:
            if len(self._callbacks[request_id]) == 0:
                # Note: only subscribe the event we concern in current process
                # to avoid flooding the event socket.
                self._sock.setsockopt(zmq.SUBSCRIBE, request_id.encode('utf-8'))
                print(f'Subscribed to request {request_id}')
            self._callbacks[request_id].add(callback)
        # Release the lock here to improve throughput.
        req_status = await requests_lib.get_request_status_async(request_id)
        if req_status is not None and callback.filter(req_status.status):
            await publish(request_id, req_status.status)

    async def close(self):
        if self._listener_task:
            self._listener_task.cancel()
        self._sock.close(linger=0)
        self._callbacks.clear()


_subscriber_lock = asyncio.Lock()
_subscriber: Optional[Subscriber] = None


async def _get_subscriber():
    global _subscriber
    if _subscriber is not None:
        return _subscriber
    async with _subscriber_lock:
        if _subscriber is None:
            _subscriber = Subscriber(_REQUEST_UPDATE_SUB_ENDPOINT)
        return _subscriber


async def subscribe(request_id: str, callback: Callback):
    subscriber = await _get_subscriber()
    await subscriber.on_request_update(request_id, callback)


def _cleanup():
    if _publisher is not None:
        asyncio.run(_publisher.close())
    if _subscriber is not None:
        asyncio.run(_subscriber.close())


atexit.register(_cleanup)

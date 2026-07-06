"""Abstract interfaces for request queue backends."""

import abc
import multiprocessing
import queue as queue_lib
import time
from typing import List, Optional, Tuple

from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.server.requests import requests as api_requests
from sky.server.requests.queues import local_queue
from sky.server.requests.queues import mp_queue
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

# The element stored inside the in-memory queues: the public request tuple
# plus the wall-clock time it was enqueued, so get() can report how long the
# entry actually waited for a worker. The enqueue timestamp is set
# afresh on every put(), including re-enqueues (requeue, retry/backoff,
# broken-pool), so the recorded wait excludes time spent paused or running.
_InternalItem = Tuple[Tuple[str, bool, bool], float]


def _observe_queue_wait(schedule_type: str, enqueued_at: float) -> None:
    """Record time the just-dequeued entry spent waiting in the queue.

    Metric emission must never disrupt the dequeue path, so any failure
    (label mismatch, client error) is logged and swallowed.
    """
    if not metrics_utils.METRICS_ENABLED:
        return
    try:
        metrics_utils.SKY_APISERVER_QUEUE_WAIT_SECONDS.labels(
            schedule_type=schedule_type).observe(
                max(0,
                    time.time() - enqueued_at))
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to observe queue wait metric: {e}')


class QueueBackend(abc.ABC):
    """Abstract queue backend.

    Each backend is responsible for emitting
    ``SKY_APISERVER_QUEUE_WAIT_SECONDS`` from ``get()`` (measuring the time
    between enqueue and dequeue of the returned entry), so the metric reflects
    true queue residency regardless of the backend's storage.
    """

    @abc.abstractmethod
    def put(self, item: Tuple[str, bool, bool]) -> None:
        """Put a (request_id, ignore_return_value, retryable) tuple."""
        raise NotImplementedError

    async def put_async(self, item: Tuple[str, bool, bool]) -> None:
        """Async version of put."""
        # By default we assume put is not blocking and can be
        # called directly in event loop
        self.put(item)

    @abc.abstractmethod
    def get(self) -> Optional[Tuple[str, bool, bool]]:
        """Non-blocking get. Returns None if queue is empty.

        Implementations must emit ``SKY_APISERVER_QUEUE_WAIT_SECONDS`` for the
        dequeued entry (see ``_observe_queue_wait``).
        """
        raise NotImplementedError

    @abc.abstractmethod
    def qsize(self) -> int:
        """Return approximate queue size."""
        raise NotImplementedError


class QueueBackendFactory(abc.ABC):
    """Creates queue instances and manages queue infrastructure."""

    @abc.abstractmethod
    def create_queue(self, schedule_type: str) -> QueueBackend:
        """Create a queue for the given schedule type.

        Args:
            schedule_type: The schedule type string (e.g., 'long', 'short').
        """
        raise NotImplementedError

    def start(self) -> Optional[multiprocessing.Process]:
        """Start any required background infrastructure.

        Returns:
            A process to join on shutdown, or None if no background process
            is needed.
        """
        return None

    def stop(self, process: Optional[multiprocessing.Process]) -> None:
        """Cleanup infrastructure."""
        if process is not None:
            process.kill()


class LocalQueueBackend(QueueBackend):
    """Process-local queue (thread-safe, no IPC)."""

    def __init__(self, queue_name: str):
        super().__init__()
        # queue_name is the schedule type ('long' / 'short'); used as the
        # metric label when reporting queue wait.
        self._schedule_type = queue_name
        self._queue = local_queue.get_queue(queue_name)

    def put(self, item: Tuple[str, bool, bool]) -> None:
        internal: _InternalItem = (item, time.time())
        self._queue.put(internal)

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        try:
            item, enqueued_at = self._queue.get(block=False)
        except queue_lib.Empty:
            return None
        _observe_queue_wait(self._schedule_type, enqueued_at)
        return item

    def qsize(self) -> int:
        return self._queue.qsize()


class MultiprocessingQueueBackend(QueueBackend):
    """Queue backed by a multiprocessing.Queue via a manager."""

    def __init__(self,
                 queue_name: str,
                 port: int = mp_queue.DEFAULT_QUEUE_MANAGER_PORT):
        super().__init__()
        # queue_name is the schedule type ('long' / 'short'); used as the
        # metric label when reporting queue wait.
        self._schedule_type = queue_name
        self._queue = mp_queue.get_queue(queue_name, port)

    def put(self, item: Tuple[str, bool, bool]) -> None:
        internal: _InternalItem = (item, time.time())
        self._queue.put(internal)

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        try:
            item, enqueued_at = self._queue.get(block=False)
        except queue_lib.Empty:
            return None
        _observe_queue_wait(self._schedule_type, enqueued_at)
        return item

    def qsize(self) -> int:
        return self._queue.qsize()


class LocalQueueFactory(QueueBackendFactory):
    """Factory for process-local queues."""

    def create_queue(self, schedule_type: str) -> QueueBackend:
        return LocalQueueBackend(schedule_type)


class MultiprocessingQueueFactory(QueueBackendFactory):
    """Factory for multiprocessing queues with a shared manager."""

    def __init__(self, port: Optional[int] = None):
        super().__init__()
        self._port = (port if port is not None else
                      mp_queue.DEFAULT_QUEUE_MANAGER_PORT)

    def create_queue(self, schedule_type: str) -> QueueBackend:
        return MultiprocessingQueueBackend(schedule_type, self._port)

    def start(self) -> Optional[multiprocessing.Process]:

        if not common_utils.is_port_available(self._port):
            raise RuntimeError(
                f'SkyPilot API server fails to start as port {self._port!r} '
                'is already in use by another process.')

        queue_names = self._get_queue_names()
        process = multiprocessing.Process(target=mp_queue.start_queue_manager,
                                          args=(queue_names, self._port))
        process.start()
        mp_queue.wait_for_queues_to_be_ready(queue_names,
                                             process,
                                             port=self._port)
        return process

    @staticmethod
    def _get_queue_names() -> List[str]:
        return [st.value for st in api_requests.ScheduleType]


_queue_backend_factory: Optional[QueueBackendFactory] = None


def get_queue_backend_factory() -> QueueBackendFactory:
    """Get the registered queue backend factory."""
    if _queue_backend_factory is not None:
        return _queue_backend_factory
    return MultiprocessingQueueFactory()


def set_queue_backend_factory(factory: QueueBackendFactory) -> None:
    """Set the queue backend factory."""
    global _queue_backend_factory
    _queue_backend_factory = factory

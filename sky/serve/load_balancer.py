"""LoadBalancer: Distribute any incoming request to all ready replicas."""
import asyncio
import copy
import logging
import threading
import time
from typing import Dict, Generic, List, Optional, Tuple, TypeVar

import aiohttp
import fastapi
import httpx
from prometheus_client import parser as prometheus_parser
from starlette import background
import uvicorn

from sky import sky_logging
from sky.serve import constants
from sky.serve import load_balancing_policies as lb_policies
from sky.serve import serve_utils
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

RequestQueueEntry = Tuple[fastapi.Request, asyncio.Event,
                          asyncio.Future[fastapi.responses.Response]]

_IS_FROM_LB_HEADER = 'X-Sky-Serve-From-LB'
_QUEUE_PROCESSOR_SLEEP_TIME = 0.01
# Whether to use "whether the inference engine queue is full or not" as an
# indicator for available replicas.
_USE_IE_QUEUE_INDICATOR = True
_IE_QUEUE_PROBE_INTERVAL = 0.2


class QueueSizeFilter(logging.Filter):

    def filter(self, record):
        return '/queue-size' not in record.getMessage()


T = TypeVar('T')


class QueueWithLock(Generic[T]):
    """A queue with an async lock to allow concurrent access."""

    def __init__(self, max_queue_size: int = 1000):
        # TODO(tian): max_queue_size.
        del max_queue_size
        self._queue: List[T] = []
        self._lock = asyncio.Lock()

    async def put(self, item: T) -> None:
        async with self._lock:
            self._queue.append(item)

    async def get(self, index: int = 0) -> T:
        async with self._lock:
            return self._queue[index]

    async def qsize(self) -> int:
        async with self._lock:
            return len(self._queue)

    async def get_and_remove(self, index: int = 0) -> T:
        async with self._lock:
            return self._queue.pop(index)

    async def empty(self) -> bool:
        async with self._lock:
            return not self._queue

    async def peek(self) -> T:
        return await self.get(0)


class ClientPool:
    """ClientPool: A pool of httpx.AsyncClient for the load balancer.

    This class is used to manage the client pool for the load balancer.
    It also incorporates the load balancing policy to select the replica.
    """

    def __init__(self, load_balancing_policy_name: Optional[str],
                 max_concurrent_requests: int) -> None:
        logger.info('Starting load balancer with policy '
                    f'{load_balancing_policy_name}.')
        # Use the registry to create the load balancing policy
        self._load_balancing_policy = lb_policies.LoadBalancingPolicy.make(
            load_balancing_policy_name)
        # TODO(tian): httpx.Client has a resource limit of 100 max connections
        # for each client. We should wait for feedback on the best max
        # connections.
        # Reference: https://www.python-httpx.org/advanced/resource-limits/
        #
        # If more than 100 requests are sent to the same replica, the
        # httpx.Client will queue the requests and send them when a
        # connection is available.
        # Reference: https://github.com/encode/httpcore/blob/a8f80980daaca98d556baea1783c5568775daadc/httpcore/_async/connection_pool.py#L69-L71 # pylint: disable=line-too-long
        self._pool: Dict[str, httpx.AsyncClient] = dict()
        # Track current active requests per replica
        self._active_requests: Dict[str, int] = dict()
        self._available_replicas: List[str] = []
        # Maximum concurrent requests per replica
        self._max_concurrent_requests = max_concurrent_requests
        # We need this lock to avoid getting from the client pool while
        # updating it from _sync_with_controller.
        self._lock: threading.Lock = threading.Lock()

    def active_requests(self) -> Dict[str, int]:
        with self._lock:
            return copy.copy(self._active_requests)

    async def refresh_with_new_urls(
            self, ready_urls: List[str]) -> List[asyncio.Task]:
        close_client_tasks = []
        with self._lock:
            await self._load_balancing_policy.set_ready_replicas(ready_urls)
            for replica_url in ready_urls:
                if replica_url not in self._pool:
                    self._pool[replica_url] = httpx.AsyncClient(
                        base_url=replica_url)
                    # Initialize active requests counter for new replicas
                    self._active_requests[replica_url] = 0
                    if replica_url not in self._available_replicas:
                        self._available_replicas.append(replica_url)
            urls_to_close = set(self._pool.keys()) - set(ready_urls)
            for replica_url in urls_to_close:
                client = self._pool.pop(replica_url)
                if replica_url in self._active_requests:
                    del self._active_requests[replica_url]
                close_client_tasks.append(client.aclose())
                if replica_url in self._available_replicas:
                    self._available_replicas.remove(replica_url)
        return close_client_tasks

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        with self._lock:
            # Get available replicas (those with capacity)
            # Only select from replicas that have capacity
            if not self._available_replicas:
                return None
            return self._load_balancing_policy.select_replica_from_subset(
                request, self._available_replicas)

    def empty(self) -> bool:
        with self._lock:
            return not self._pool

    def ready_replicas(self) -> List[str]:
        return self._load_balancing_policy.ready_replicas

    def get_client(self, url: str) -> Optional[httpx.AsyncClient]:
        with self._lock:
            return self._pool.get(url, None)

    def set_replica_available_no_lock(self, url: str) -> None:
        if url not in self._available_replicas:
            self._available_replicas.append(url)

    def set_replica_available(self, url: str) -> None:
        with self._lock:
            self.set_replica_available_no_lock(url)

    def set_replica_unavailable_no_lock(self, url: str) -> None:
        if url in self._available_replicas:
            self._available_replicas.remove(url)

    def set_replica_unavailable(self, url: str) -> None:
        with self._lock:
            self.set_replica_unavailable_no_lock(url)

    def pre_execute_hook(self, url: str, request: fastapi.Request) -> None:
        with self._lock:
            logger.info(f'Active requests: {self._active_requests}, '
                        f'Available replicas: {self._available_replicas}')
            self._load_balancing_policy.pre_execute_hook(url, request)
            self._active_requests[url] = self._active_requests.get(url, 0) + 1
            if _USE_IE_QUEUE_INDICATOR:
                return
            if self._active_requests[url] >= self._max_concurrent_requests:
                self.set_replica_unavailable_no_lock(url)

    def post_execute_hook(self, url: str, request: fastapi.Request) -> None:
        with self._lock:
            self._load_balancing_policy.post_execute_hook(url, request)
            if url in self._active_requests and self._active_requests[url] > 0:
                self._active_requests[url] -= 1
                if _USE_IE_QUEUE_INDICATOR:
                    return
                if self._active_requests[url] < self._max_concurrent_requests:
                    self.set_replica_available_no_lock(url)


class SkyServeLoadBalancer:
    """SkyServeLoadBalancer: distribute incoming traffic with proxy.

    This class accept any traffic to the controller and proxies it
    to the appropriate endpoint replica according to the load balancing
    policy.
    """

    def __init__(self,
                 controller_url: str,
                 load_balancer_port: int,
                 load_balancing_policy_name: Optional[str] = None,
                 meta_load_balancing_policy_name: Optional[str] = None,
                 region: Optional[str] = None,
                 tls_credential: Optional[serve_utils.TLSCredential] = None,
                 max_concurrent_requests: int = 10,
                 max_queue_size: int = 1000,
                 is_local_debug_mode: bool = False) -> None:
        """Initialize the load balancer.

        Args:
            controller_url: The URL of the controller.
            load_balancer_port: The port where the load balancer listens to.
            load_balancing_policy_name: The name of the load balancing policy
                to use. Defaults to None.
            meta_load_balancing_policy_name: The name of the load balancing
                policy for load balancers. Defaults to None.
            region: The region of the load balancer. Defaults to None.
            tls_credential: The TLS credentials for HTTPS endpoint. Defaults
                to None.
            max_concurrent_requests: Maximum concurrent requests per replica.
                Defaults to 10.
            max_queue_size: Maximum size of the request queue. Defaults to 1000.
        """
        self._app: fastapi.FastAPI = fastapi.FastAPI()
        self._controller_url: str = controller_url
        self._load_balancer_port: int = load_balancer_port
        self._request_aggregator: serve_utils.RequestsAggregator = (
            serve_utils.RequestTimestamp())
        self._region: Optional[str] = region
        self._tls_credential: Optional[serve_utils.TLSCredential] = (
            tls_credential)
        self._max_concurrent_requests: int = max_concurrent_requests
        self._load_balancing_policy_name: Optional[str] = (
            load_balancing_policy_name)
        self._replica_pool: ClientPool = ClientPool(load_balancing_policy_name,
                                                    max_concurrent_requests)
        self._lb_pool: ClientPool = ClientPool(meta_load_balancing_policy_name,
                                               max_concurrent_requests)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._workload_steal_session: Optional[aiohttp.ClientSession] = None
        self._request_queue: Optional[QueueWithLock[RequestQueueEntry]] = None
        self._tasks: List[asyncio.Task] = []
        self._max_queue_size: int = max_queue_size
        self._external_host: str = serve_utils.get_external_host()
        self._handle_request_tasks: List[asyncio.Task] = []
        # TODO(tian): Temporary debugging solution. Remove this in production.
        self._replica2id: Dict[str, str] = {}
        self._lb2region: Dict[str, str] = {}
        self._is_local_debug_mode = is_local_debug_mode

    async def _sync_with_controller(self):
        """Sync with controller periodically.

        Every `constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS` seconds, the
        load balancer will sync with the controller to get the latest
        information about available replicas; also, it report the request
        information to the controller, so that the controller can make
        autoscaling decisions.
        """
        # Sleep for a while to wait the controller bootstrap.
        await asyncio.sleep(5)

        while True:
            close_client_tasks = []
            async with aiohttp.ClientSession() as session:
                try:
                    # Send request information
                    async with session.post(
                            self._controller_url +
                            '/controller/load_balancer_sync',
                            json={
                                'request_aggregator':
                                    self._request_aggregator.to_dict()
                            },
                            timeout=aiohttp.ClientTimeout(5),
                    ) as response:
                        # Clean up after reporting request info to avoid OOM.
                        self._request_aggregator.clear()
                        response.raise_for_status()
                        response_json = await response.json()
                        ready_replica_urls = response_json.get(
                            'ready_replica_urls', {})
                        ready_lb_urls = response_json.get('ready_lb_urls', {})
                except aiohttp.ClientError as e:
                    logger.error('An error occurred when syncing with '
                                 f'the controller: {e}')
                else:
                    # TODO(tian): Check if there is any replica that is not
                    # assigned a LB.
                    logger.info(f'All ready replica URLs: {ready_replica_urls}')
                    logger.info(f'All ready LB URLs: {ready_lb_urls}')
                    ready_urls = ready_replica_urls.get(self._region, [])
                    logger.info(f'Available Replica URLs: {ready_replica_urls},'
                                f' Ready URLs in local region {self._region}: '
                                f'{ready_urls}')
                    close_client_tasks.extend(
                        await
                        self._replica_pool.refresh_with_new_urls(ready_urls))
                    for rurl in ready_urls:
                        if rurl not in self._replica2id:
                            self._replica2id[rurl] = str(len(self._replica2id))
                    # For LB, we dont need to separate them by region.
                    all_lb_urls = sum(ready_lb_urls.values(), [])
                    for r, lbs in ready_lb_urls.items():
                        for lb in lbs:
                            self._lb2region[lb] = r
                    logger.info(f'Available LB URLs: {all_lb_urls}')
                    close_client_tasks.extend(
                        await self._lb_pool.refresh_with_new_urls(all_lb_urls))

            await asyncio.sleep(constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS)
            # Await those tasks after the interval to avoid blocking.
            await asyncio.gather(*close_client_tasks)

    async def _proxy_request_to(
            self, pool_to_use: ClientPool, proxy_request: httpx.Request,
            client: httpx.AsyncClient, url: str, request: fastapi.Request,
            extra_header: Dict[str, str]) -> fastapi.responses.Response:
        """Proxy the request to the specified URL.

        Returns:
            The response from the endpoint replica. Return the exception
            encountered if anything goes wrong.
        """
        logger.info(f'Proxy request to {url}')
        proxy_response = await client.send(proxy_request, stream=True)

        async def background_func():
            await proxy_response.aclose()
            pool_to_use.post_execute_hook(url, request)

        proxy_response.headers.update(extra_header)
        return fastapi.responses.StreamingResponse(
            content=proxy_response.aiter_raw(),
            status_code=proxy_response.status_code,
            headers=proxy_response.headers,
            background=background.BackgroundTask(background_func))

    async def _request_finish_callback(self, pool_to_use: ClientPool,
                                       proxy_request: httpx.Request,
                                       client: httpx.AsyncClient, url: str,
                                       request: fastapi.Request,
                                       extra_header: Dict[str, str],
                                       response_future: asyncio.Future[
                                           fastapi.responses.Response],
                                       request_event: asyncio.Event) -> None:
        response = await self._proxy_request_to(pool_to_use, proxy_request,
                                                client, url, request,
                                                extra_header)
        response_future.set_result(response)
        request_event.set()

    async def _handle_requests(self, url: str, entry: RequestQueueEntry,
                               is_from_lb: bool) -> None:
        """Handle the request."""
        assert self._loop is not None
        try:
            request, request_event, response_future = entry
            pool_to_use = self._lb_pool if is_from_lb else self._replica_pool
            pool_to_use.pre_execute_hook(url, request)
            # We defer the get of the client here on purpose, for case when the
            # replica is ready in `_put_request_to_queue` but refreshed before
            # entering this function. In that case we will return an error here
            # and retry to find next ready replica. We also need to wait for the
            # update of the client pool to finish before getting the client.
            client = pool_to_use.get_client(url)
            if client is None:
                response_future.set_exception(
                    RuntimeError(f'Client for {url} not found.'))
                request_event.set()
                return
            worker_url = httpx.URL(path=request.url.path,
                                   query=request.url.query.encode('utf-8'))
            headers = request.headers.mutablecopy()
            if is_from_lb:
                # If it is not from LB, then the following request will be sent
                # out from LB. So we add the header to indicate it.
                headers[_IS_FROM_LB_HEADER] = 'true'
            proxy_request = client.build_request(
                request.method,
                worker_url,
                headers=headers.raw,
                content=await request.body(),
                timeout=constants.LB_STREAM_TIMEOUT)
            extra_header = {}
            if not is_from_lb:
                replica_id = self._replica2id.get(url, 'N/A')
                extra_header['replica-decision'] = (
                    f'Select Replica with id {replica_id} ({url})')
            else:
                lb_region = self._lb2region.get(url, 'N/A')
                extra_header['lb-decision'] = (
                    f'Select LB in region {lb_region} ({url})')
            coro = self._request_finish_callback(pool_to_use, proxy_request,
                                                 client, url, request,
                                                 extra_header, response_future,
                                                 request_event)
            self._handle_request_tasks.append(self._loop.create_task(coro))
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f'Error when proxy request to {url}: '
                         f'{common_utils.format_exception(e)}')
            return e

    def _steal_targets(self) -> Tuple[List[str], Optional[str]]:
        """Return the target LBs to steal from."""
        steal_targets = []
        self_url = None
        all_lb_urls = self._lb_pool.ready_replicas()
        for lb in all_lb_urls:
            # NOTE(tian): Hack for local debugging. For SkyServe deployment,
            # all external load balancers will get the same port. However, in
            # local debug deployment, we spin up multiple load balancers with
            # different ports. Hence we only check the port if the host is
            # 0.0.0.0 (local deployment).
            if self._external_host not in lb and (
                (not self._is_local_debug_mode) or
                    str(self._load_balancer_port) not in lb):
                steal_targets.append(lb)
            else:
                assert self_url is None, (self_url, lb, all_lb_urls)
                self_url = lb
        return steal_targets, self_url

    async def _queue_processor(self) -> None:
        """Background task to process queued requests."""
        assert self._loop is not None
        assert self._request_queue is not None
        logger.info('Starting request queue processor')
        while True:
            await asyncio.sleep(_QUEUE_PROCESSOR_SLEEP_TIME)
            try:
                if await self._request_queue.empty():
                    continue
                # logger.info('Length of request queue for '
                #             f'{self._external_host}: '
                #             f'{await self._request_queue.qsize()}')

                # Peek at the first item in the queue without removing it
                entry: RequestQueueEntry = await self._request_queue.peek()
                request, request_event, response_future = entry

                pool_to_use = self._replica_pool
                source_identity = 'User'

                # TODO(tian): Handle no replica case.
                if pool_to_use.empty():
                    await self._request_queue.get_and_remove()
                    exception = fastapi.HTTPException(
                        # 503 means that the server is currently
                        # unable to handle the incoming requests.
                        status_code=503,
                        detail=('No ready replicas. '
                                'Use "sky serve status [SERVICE_NAME]" '
                                'to check the replica status.'))
                    response_future.set_exception(exception)
                    request_event.set()

                # Attempt to find an available replica
                ready_replica_url = pool_to_use.select_replica(request)

                if ready_replica_url is not None:
                    # Process the request if a replica is available
                    # Now we can safely remove it from the queue
                    await self._request_queue.get_and_remove()
                    try:
                        logger.info(f'Processing queued request {request.url} '
                                    f'from {source_identity} to '
                                    f'{ready_replica_url}.')
                        await self._handle_requests(ready_replica_url,
                                                    entry,
                                                    is_from_lb=False)
                    except Exception as e:  # pylint: disable=broad-except
                        # Set exception to propagate to the waiting handler
                        response_future.set_exception(e)
                        request_event.set()

            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in queue processor: '
                             f'{common_utils.format_exception(e)}')

    async def _request_stealing_loop(self) -> None:
        """Background task to process request stealing."""
        assert self._loop is not None
        assert self._workload_steal_session is not None
        assert self._request_queue is not None
        logger.info('Starting request stealing loop')
        while True:
            await asyncio.sleep(_QUEUE_PROCESSOR_SLEEP_TIME)
            try:
                if not await self._request_queue.empty():
                    continue
                # Don't steal if there is no replica.
                if not self._replica_pool.ready_replicas():
                    continue
                steal_targets, self_url = self._steal_targets()
                # logger.info(f'Steal targets: {steal_targets}, '
                #             f'self_url: {self_url}')
                # It is possible that self_url is not ready in the LB
                # replica manager yet. We wait until it is ready.
                if steal_targets and self_url is not None:
                    # logger.info('Request queue is empty. Try to '
                    #             f'steal from {steal_targets}')
                    steal_target = steal_targets[0]
                    if len(steal_targets) > 1:
                        logger.error('More than one steal target. '
                                     'Select the first one '
                                     f'({steal_target}).')
                    # TODO(tian): Use urlparse for robustness.
                    # TODO(tian): Investigate this pylint warning.
                    async with self._workload_steal_session.get(  # pylint: disable=not-async-context-manager
                            steal_target + '/queue-size') as response:
                        queue_size = (await response.json())['queue_size']
                    # logger.info(f'Queue size of {steal_target}: '
                    #             f'{queue_size}')
                    if queue_size <= 0:
                        continue
                    num_steal = queue_size // 2
                    if num_steal <= 0:
                        continue
                    logger.info(f'Steal from {steal_target} with '
                                f'{num_steal}/{queue_size} requests.')
                    async with self._workload_steal_session.post(  # pylint: disable=not-async-context-manager
                            steal_target + '/steal-request',
                            json={
                                'num_steal': num_steal,
                                'source_lb_url': self_url,
                            }) as steal_response:
                        steal_response.raise_for_status()
                        remaining_to_steal = (
                            await steal_response.json())['remaining_to_steal']
                        actual_num_steal = num_steal - remaining_to_steal
                        logger.info(f'Actual steal: '
                                    f'{actual_num_steal}/{num_steal} '
                                    f'from {steal_target}.')
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in request stealing loop: '
                             f'{common_utils.format_exception(e)}')

    async def _cleanup_completed_tasks(self) -> None:
        """Cleanup completed tasks."""
        while True:
            for task in self._handle_request_tasks:
                if task.done():
                    self._handle_request_tasks.remove(task)
            await asyncio.sleep(_QUEUE_PROCESSOR_SLEEP_TIME)

    async def _put_request_to_queue(
            self, request: fastapi.Request) -> fastapi.responses.Response:
        """Queue the request for processing by the queue processor."""
        assert self._request_queue is not None
        self._request_aggregator.add(request)
        logger.info(f'Received request {request.url}.')

        try:
            # Create future and event in the current event loop context
            assert self._loop is not None
            response_future: asyncio.Future[
                fastapi.responses.Response] = self._loop.create_future()
            request_event: asyncio.Event = asyncio.Event()
            entry: RequestQueueEntry = (request, request_event, response_future)

            # Queue the request for processing
            await self._request_queue.put(entry)

            # Wait for the request to be processed by the queue processor
            await request_event.wait()

            # Get the result or exception
            assert response_future.done(), (
                'Request processing completed but future not set')
            return await response_future

        except asyncio.TimeoutError:
            # Queue is full
            return fastapi.responses.Response(
                status_code=429,
                content='Too many requests. Queue is full. '
                'Please try again later.')
        except Exception as e:
            # Other errors during queue processing
            logger.error(f'Error processing queued request: '
                         f'{common_utils.format_exception(e)}')
            raise fastapi.HTTPException(
                status_code=500, detail=f'Error processing request: {str(e)}')

    async def _health_check(self) -> fastapi.responses.Response:
        """Health check endpoint."""
        return fastapi.responses.Response(status_code=200)

    async def _queue_size(self) -> fastapi.responses.Response:
        """Return the size of the request queue."""
        assert self._request_queue is not None
        num = 0
        # TODO(tian): Maintain a counter on this.
        for i in range(await self._request_queue.qsize()):
            request, _, _ = await self._request_queue.get(i)
            if not request.headers.get(_IS_FROM_LB_HEADER, False):
                num += 1
        return fastapi.responses.JSONResponse(status_code=200,
                                              content={'queue_size': num})

    async def _raw_queue_size(self) -> fastapi.responses.Response:
        """Return the size of the request queue."""
        assert self._request_queue is not None
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={'queue_size': await self._request_queue.qsize()})

    async def _replica_queue(self) -> fastapi.responses.Response:
        """Return the request queue for each replica."""
        assert self._request_queue is not None
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={'replica_queue': self._replica_pool.active_requests()})

    async def _configuration(self) -> fastapi.responses.Response:
        """Return the configuration of the load balancer."""
        assert self._request_queue is not None
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={
                'queue_size': await self._request_queue.qsize(),
                'replica_queue': self._replica_pool.active_requests(),
                'max_queue_size': self._max_queue_size,
                'max_concurrent_requests': self._max_concurrent_requests,
                'load_balancing_policy_name': self._load_balancing_policy_name
            })

    async def _steal_request(
            self, steal_request: fastapi.Request) -> fastapi.responses.Response:
        """Let other LBs steal requests from this LB."""
        assert self._loop is not None
        assert self._request_queue is not None
        req_json = await steal_request.json()
        num_steal = req_json['num_steal']
        source_lb_url = req_json['source_lb_url']
        logger.info(f'Received steal request from {source_lb_url} with '
                    f'{num_steal} requests.')
        for i in range(await self._request_queue.qsize()):
            # Re-getting the queue size since it is possible the queue changed
            # during the loop, e.g. a request from head is popped out.
            idx = await self._request_queue.qsize() - 1 - i
            if idx < 0:
                break
            entry = await self._request_queue.get(idx)
            request, _, _ = entry
            # Check if the request is from the source LB.
            if request.headers.get(_IS_FROM_LB_HEADER, False):
                continue
            await self._request_queue.get_and_remove(idx)
            await self._handle_requests(source_lb_url, entry, is_from_lb=True)
            num_steal -= 1
            if not num_steal:
                break
        if num_steal != 0:
            logger.info(f'{num_steal} requests was NOT stolen and remained '
                        f'from {source_lb_url}.')

        # if num_steal:
        #     raise fastapi.HTTPException(
        #         status_code=429,
        #         detail=f'Not enough requests to steal. '
        #         f'Requested {num_steal}, but only {num_steal} requests '
        #         f'available.')
        return fastapi.responses.JSONResponse(
            status_code=200, content={'remaining_to_steal': num_steal})

    async def _probe_ie_queue_one_replica(
            self, replica: str) -> Tuple[Optional[float], str]:
        """Probe the inference engine queue of one replica."""
        # TODO(tian): Support other inference engines.
        assert self._workload_steal_session is not None
        try:
            # TODO(tian): Use urlparse for robustness, and change the session
            # name since this is no longer only used for stealing requests.
            async with self._workload_steal_session.get(  # pylint: disable=not-async-context-manager
                    replica + '/metrics') as response:
                metrics_text = await response.text()
            metrics = prometheus_parser.text_string_to_metric_families(
                metrics_text)
            for f in metrics:
                if f.name in [
                        'vllm:num_requests_waiting', 'sglang:num_queue_reqs'
                ]:
                    return f.samples[0].value, replica
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error probing inference engine queue of {replica}: '
                         f'{common_utils.format_exception(e)}')
        return None, replica

    async def _probe_ie_queue(self) -> None:
        """Probe the inference engine queue."""
        assert self._loop is not None
        assert self._request_queue is not None
        while True:
            await asyncio.sleep(_IE_QUEUE_PROBE_INTERVAL)
            time_start = time.perf_counter()
            tasks = []
            for replica in self._replica_pool.ready_replicas():
                tasks.append(self._probe_ie_queue_one_replica(replica))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            time_end_probe = time.perf_counter()
            time_start_set_replica = time.perf_counter()
            for queue_size, replica in results:
                if isinstance(queue_size, Exception):
                    logger.error(f'Error probing inference engine queue: '
                                 f'{common_utils.format_exception(queue_size)}')
                elif queue_size is None:
                    logger.error(f'No inference engine queue size found for '
                                 f'{replica}.')
                else:
                    logger.debug(f'Inference engine queue size for {replica}: '
                                 f'{queue_size}')
                    if queue_size > 0:
                        self._replica_pool.set_replica_unavailable(replica)
                    else:
                        self._replica_pool.set_replica_available(replica)
            time_end_set_replica = time.perf_counter()
            time_elapsed_set_replica = (time_end_set_replica -
                                        time_start_set_replica)
            logger.debug(f'Probe time: {time_end_probe - time_start}s, '
                         f'Set replica time: {time_elapsed_set_replica}s.')

    def run(self):
        # Add health check endpoint first so it takes precedence
        self._app.add_api_route(constants.LB_HEALTH_ENDPOINT,
                                self._health_check,
                                methods=['GET'])

        self._app.add_api_route('/queue-size',
                                self._queue_size,
                                methods=['GET'])

        self._app.add_api_route('/raw-queue-size',
                                self._raw_queue_size,
                                methods=['GET'])

        self._app.add_api_route('/replica-queue',
                                self._replica_queue,
                                methods=['GET'])

        self._app.add_api_route('/conf', self._configuration, methods=['GET'])

        self._app.add_api_route('/steal-request',
                                self._steal_request,
                                methods=['POST'])

        self._app.add_api_route('/{path:path}',
                                self._put_request_to_queue,
                                methods=['GET', 'POST', 'PUT', 'DELETE'])

        @self._app.on_event('startup')
        async def startup():
            # Configure logger
            uvicorn_access_logger = logging.getLogger('uvicorn.access')
            for handler in uvicorn_access_logger.handlers:
                handler.setFormatter(sky_logging.FORMATTER)
                handler.addFilter(QueueSizeFilter())
            # # Make sure we're using the current event loop
            self._loop = asyncio.get_running_loop()
            self._workload_steal_session = aiohttp.ClientSession()
            self._request_queue = QueueWithLock(self._max_queue_size)

            # Register controller synchronization task
            self._tasks.append(
                self._loop.create_task(self._sync_with_controller()))

            # Start the request queue processor
            self._tasks.append(self._loop.create_task(self._queue_processor()))

            # Start the task to cleanup completed tasks
            self._tasks.append(
                self._loop.create_task(self._cleanup_completed_tasks()))

            # Start the request stealing loop
            self._tasks.append(
                self._loop.create_task(self._request_stealing_loop()))

            if _USE_IE_QUEUE_INDICATOR:
                self._tasks.append(
                    self._loop.create_task(self._probe_ie_queue()))

        @self._app.on_event('shutdown')
        async def shutdown():
            # Cancel all tasks
            tasks_to_cancel = []
            for task in self._tasks:
                if not task.done():
                    task.cancel()
                    tasks_to_cancel.append(task)
            if tasks_to_cancel:
                try:
                    await asyncio.gather(*tasks_to_cancel,
                                         return_exceptions=True)
                except asyncio.CancelledError:
                    pass

            logger.info('All tasks successfully cancelled')

        uvicorn_tls_kwargs = ({} if self._tls_credential is None else
                              self._tls_credential.dump_uvicorn_kwargs())

        protocol = 'https' if self._tls_credential is not None else 'http'

        logger.info('SkyServe Load Balancer started on '
                    f'{protocol}://0.0.0.0:{self._load_balancer_port}')

        uvicorn.run(self._app,
                    host='0.0.0.0',
                    port=self._load_balancer_port,
                    **uvicorn_tls_kwargs)


def run_load_balancer(
    controller_addr: str,
    load_balancer_port: int,
    load_balancing_policy_name: Optional[str] = None,
    meta_load_balancing_policy_name: Optional[str] = None,
    region: Optional[str] = None,
    tls_credential: Optional[serve_utils.TLSCredential] = None,
    max_concurrent_requests: int = 10,
    max_queue_size: int = 1000,
) -> None:
    """ Run the load balancer.

    Args:
        controller_addr: The address of the controller.
        load_balancer_port: The port where the load balancer listens to.
        load_balancing_policy_name: The name of the load balancing policy
            to use. Defaults to None.
        meta_load_balancing_policy_name: The name of the load balancing policy
            for load balancers. Defaults to None.
        region: The region of the load balancer. Defaults to None.
        tls_credential: The TLS credential for HTTPS endpoint. Defaults to None.
        max_concurrent_requests: Maximum concurrent requests per replica.
            Defaults to 10.
        max_queue_size: Maximum size of the request queue. Defaults to 1000.
    """
    load_balancer = SkyServeLoadBalancer(
        controller_url=controller_addr,
        load_balancer_port=load_balancer_port,
        load_balancing_policy_name=load_balancing_policy_name,
        meta_load_balancing_policy_name=meta_load_balancing_policy_name,
        region=region,
        tls_credential=tls_credential,
        max_concurrent_requests=max_concurrent_requests,
        max_queue_size=max_queue_size,
    )
    load_balancer.run()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller-addr',
                        required=True,
                        default='127.0.0.1',
                        help='The address of the controller.')
    parser.add_argument('--load-balancer-port',
                        type=int,
                        required=True,
                        default=8890,
                        help='The port where the load balancer listens to.')
    available_policies = list(lb_policies.LB_POLICIES.keys())
    parser.add_argument(
        '--load-balancing-policy',
        choices=available_policies,
        default=lb_policies.DEFAULT_LB_POLICY,
        help=f'The load balancing policy to use. Available policies: '
        f'{", ".join(available_policies)}.')
    parser.add_argument(
        '--meta-load-balancing-policy',
        choices=available_policies,
        default=lb_policies.DEFAULT_LB_POLICY,
        help=f'The meta load balancing policy to use. Available policies: '
        f'{", ".join(available_policies)}.')
    parser.add_argument('--region',
                        required=True,
                        help='The region of the load balancer.')
    parser.add_argument('--max-concurrent-requests',
                        type=int,
                        default=10,
                        help='Maximum concurrent requests per replica.')
    parser.add_argument('--max-queue-size',
                        type=int,
                        default=1000,
                        help='Maximum size of the request queue.')
    args = parser.parse_args()
    run_load_balancer(
        args.controller_addr,
        args.load_balancer_port,
        args.load_balancing_policy,
        args.meta_load_balancing_policy,
        args.region,
        tls_credential=None,
        max_concurrent_requests=args.max_concurrent_requests,
        max_queue_size=args.max_queue_size,
    )

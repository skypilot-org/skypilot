"""LoadBalancer: Distribute any incoming request to all ready replicas."""
# pylint: disable=line-too-long
import asyncio
import collections
import copy
import dataclasses
import logging
import os
import time
import traceback
from typing import Dict, Generic, List, Optional, Tuple, TypeVar, Union

import aiohttp
import fastapi
import httpx
from prometheus_client import parser as prometheus_parser
from starlette import background
from starlette import requests as starlette_requests
import uvicorn

from sky import sky_logging
from sky.serve import constants
from sky.serve import load_balancing_policies as lb_policies
from sky.serve import serve_utils
from sky.utils import common_utils
from sky.utils import env_options

logger = sky_logging.init_logger(__name__)

_IS_FROM_LB_HEADER = 'X-Sky-Serve-From-LB'
_QUEUE_SIZE_HEADER = 'X-Sky-Serve-Queue-Size'
_QUEUE_PROCESSOR_SLEEP_TIME = 0.01
_IE_QUEUE_PROBE_INTERVAL = 0.1
_STEAL_TRIGGER_THRESHOLD = 10
# _MIN_STEAL_INTERVAL = 1.0
# _MAX_CACHE_HIT_DELAY_TIMES = 3

# Whether to push requests across load balancers.
_DO_PUSHING_ACROSS_LB = env_options.Options.DO_PUSHING_ACROSS_LB.get()
# Use load balancing, or use dynamic rate limiting.
# Currently we forcely override this to True to do the actual "pushing".
# Not that kind of similar to pulling.
_LB_PUSHING_ENABLE_LB = env_options.Options.LB_PUSHING_ENABLE_LB.get()
_DO_PUSHING_TO_REPLICA = env_options.Options.DO_PUSHING_TO_REPLICA.get()
_USE_V2_STEALING = env_options.Options.USE_V2_STEALING.get()
_DISABLE_LEAST_LOAD_IN_PREFIX = env_options.Options.DISABLE_LEAST_LOAD_IN_PREFIX.get(
)
_USE_IE_QUEUE_INDICATOR = env_options.Options.USE_IE_QUEUE_INDICATOR.get()
_ENABLE_SELECTIVE_PUSHING = env_options.Options.ENABLE_SELECTIVE_PUSHING.get()
_FORCE_DISABLE_STEALING = env_options.Options.FORCE_DISABLE_STEALING.get()


@dataclasses.dataclass
class RequestEntry:
    """Single entry in the request queue."""
    id: int
    time_arrive: float
    time_scheduled: Optional[float]
    request: fastapi.Request
    request_event: asyncio.Event
    response_future: asyncio.Future[fastapi.responses.Response]

    def set_failed_on(self, e: Exception) -> None:
        self.response_future.set_exception(e)
        self.request_event.set()


@dataclasses.dataclass
class StealEntry:
    """Entry to store metrics used when sorting the requests to be stolen."""
    id: int
    matched_rate: float
    matched_length: int
    matched_steal_target: bool
    queue_idx: int

    def __lt__(self, other: 'StealEntry') -> bool:
        if self.matched_steal_target:
            if not other.matched_steal_target:
                return True
            # If both are steal targets, prioritize the one with higher
            # match rate.
            return self.matched_rate > other.matched_rate
        if other.matched_steal_target:
            return False
        # Prioritize requests in the end of the queue if both
        # are not steal targets.
        return self.queue_idx > other.queue_idx
        # For non-steal target, we want to keep the high match rate requests
        # to be stolen.
        # return self.matched_rate < other.matched_rate


@dataclasses.dataclass
class LBConfigEntry:
    """Entry to store the load balancer configuration."""
    queue_size: int
    queue_size_actual: int
    num_replicas: int
    replica_queue_size_total: int


StealBarrierEntry = str
RequestQueueEntry = Union[RequestEntry, StealBarrierEntry]


class QueueSizeFilter(logging.Filter):

    def filter(self, record):
        return '/queue-size' not in record.getMessage()


class ConfFilter(logging.Filter):

    def filter(self, record):
        return '/conf' not in record.getMessage()


T = TypeVar('T')


class QueueWithLock(Generic[T]):
    """A queue with an async lock to allow concurrent access."""

    def __init__(self) -> None:
        self._queue: List[T] = []
        self._lock = asyncio.Lock()
        self._actual_size = 0

    async def put(self, item: T) -> None:
        async with self._lock:
            if isinstance(item, RequestEntry):
                if not item.request.headers.get(_IS_FROM_LB_HEADER, False):
                    self._actual_size += 1
            self._queue.append(item)

    async def put_in_head(self, item: T) -> None:
        async with self._lock:
            if isinstance(item, RequestEntry):
                if not item.request.headers.get(_IS_FROM_LB_HEADER, False):
                    self._actual_size += 1
            self._queue.insert(0, item)

    async def get(self, index: int = 0) -> T:
        async with self._lock:
            return self._queue[index]

    async def qsize(self) -> int:
        async with self._lock:
            return len(self._queue)

    async def get_and_remove(self, index: int = 0) -> T:
        async with self._lock:
            item = self._queue.pop(index)
            if isinstance(item, RequestEntry):
                if not item.request.headers.get(_IS_FROM_LB_HEADER, False):
                    self._actual_size -= 1
            return item

    async def empty(self) -> bool:
        async with self._lock:
            return not self._queue

    async def peek(self) -> T:
        return await self.get(0)

    async def actual_size(self) -> int:
        async with self._lock:
            return self._actual_size


@dataclasses.dataclass
class PoolEntry:
    client: httpx.AsyncClient
    latency: float


class ClientPool:
    """ClientPool: A pool of httpx.AsyncClient for the load balancer.

    This class is used to manage the client pool for the load balancer.
    It also incorporates the load balancing policy to select the replica.
    """

    def __init__(self,
                 load_balancing_policy_name: Optional[str],
                 max_concurrent_requests: int,
                 use_ie_queue_indicator: bool,
                 enable_latency_check: bool = False) -> None:
        logger.info('Starting load balancer with policy '
                    f'{load_balancing_policy_name}.')
        # Use the registry to create the load balancing policy
        self._load_balancing_policy = lb_policies.LoadBalancingPolicy.make(
            load_balancing_policy_name)
        if _DISABLE_LEAST_LOAD_IN_PREFIX and isinstance(
                self._load_balancing_policy, lb_policies.PrefixTreePolicy):
            self._load_balancing_policy.disbale_least_load_fallback()
        # TODO(tian): httpx.Client has a resource limit of 100 max connections
        # for each client. We should wait for feedback on the best max
        # connections.
        # Reference: https://www.python-httpx.org/advanced/resource-limits/
        #
        # If more than 100 requests are sent to the same replica, the
        # httpx.Client will queue the requests and send them when a
        # connection is available.
        # Reference: https://github.com/encode/httpcore/blob/a8f80980daaca98d556baea1783c5568775daadc/httpcore/_async/connection_pool.py#L69-L71 # pylint: disable=line-too-long
        self._pool: Dict[str, PoolEntry] = dict()
        # Track current active requests per replica
        self._active_requests: Dict[str, int] = dict()
        self._available_replicas: List[str] = []
        # Maximum concurrent requests per replica
        self._max_concurrent_requests = max_concurrent_requests
        # We need this lock to avoid getting from the client pool while
        # updating it from _sync_with_controller.
        self._lock: asyncio.Lock = asyncio.Lock()
        self._use_ie_queue_indicator = use_ie_queue_indicator
        self._enable_latency_check = enable_latency_check

    def enable_load_balancing(self) -> None:
        if isinstance(self._load_balancing_policy,
                      lb_policies.PrefixTreePolicy):
            self._load_balancing_policy.enable_load_balancing()

    async def re_init_lock(self) -> None:
        self._lock = asyncio.Lock()

    async def background_task(self) -> None:
        await self._load_balancing_policy.background_task()

    async def active_requests(self) -> Dict[str, int]:
        async with self._lock:
            return copy.copy(self._active_requests)

    async def set_replica_latency(self, url: str) -> None:
        latency = await serve_utils.check_lb_latency(url)
        if latency is not None:
            async with self._lock:
                self._pool[url].latency = latency

    async def refresh_with_new_urls(
            self, ready_urls: List[str]) -> List[asyncio.Task]:
        tasks = []
        async with self._lock:
            await self._load_balancing_policy.set_ready_replicas(ready_urls)
            for replica_url in ready_urls:
                if replica_url not in self._pool:
                    self._pool[replica_url] = PoolEntry(
                        httpx.AsyncClient(base_url=replica_url), float('inf'))
                    if self._enable_latency_check:
                        tasks.append(
                            asyncio.create_task(
                                self.set_replica_latency(replica_url)))
                    # Initialize active requests counter for new replicas
                    self._active_requests[replica_url] = 0
                    if replica_url not in self._available_replicas:
                        self._available_replicas.append(replica_url)
            urls_to_close = set(self._pool.keys()) - set(ready_urls)
            for replica_url in urls_to_close:
                client = self._pool.pop(replica_url)
                if replica_url in self._active_requests:
                    del self._active_requests[replica_url]
                if client is not None:
                    tasks.append(asyncio.create_task(client.client.aclose()))
                if replica_url in self._available_replicas:
                    self._available_replicas.remove(replica_url)
        return tasks

    async def select_replica(self, request: fastapi.Request,
                             **kwargs) -> Optional[str]:
        async with self._lock:
            # Get available replicas (those with capacity)
            # Only select from replicas that have capacity
            if not self._available_replicas:
                return None
            return await self._load_balancing_policy.select_replica_from_subset(
                request, self._available_replicas, **kwargs)

    async def empty(self) -> bool:
        async with self._lock:
            return not self._pool

    def ready_replicas(self) -> List[str]:
        return self._load_balancing_policy.ready_replicas

    async def available_replicas(self) -> List[str]:
        async with self._lock:
            return self._available_replicas

    async def get_client(self, url: str) -> Optional[httpx.AsyncClient]:
        async with self._lock:
            entry = self._pool.get(url, None)
            if entry is None:
                return None
            return entry.client

    async def get_latency(self, url: str) -> Optional[float]:
        async with self._lock:
            entry = self._pool.get(url, None)
            if entry is None:
                return None
            return entry.latency

    def set_replica_available_no_lock(self, url: str) -> None:
        if url not in self._available_replicas:
            self._available_replicas.append(url)

    async def set_replica_available(self, url: str) -> None:
        async with self._lock:
            self.set_replica_available_no_lock(url)

    def set_replica_unavailable_no_lock(self, url: str) -> None:
        if url in self._available_replicas:
            self._available_replicas.remove(url)

    async def set_replica_unavailable(self, url: str) -> None:
        async with self._lock:
            self.set_replica_unavailable_no_lock(url)

    async def set_all_replicas_unavailable_except(self,
                                                  urls: List[str]) -> None:
        async with self._lock:
            self._available_replicas = [
                url for url in self._available_replicas if url in urls
            ]

    async def pre_execute_hook(self, url: str,
                               request: fastapi.Request) -> None:
        async with self._lock:
            # logger.info(f'Active requests: {self._active_requests}, '
            #             f'Available replicas: {self._available_replicas}')
            await self._load_balancing_policy.pre_execute_hook(url, request)
            self._active_requests[url] = self._active_requests.get(url, 0) + 1
            if self._use_ie_queue_indicator:
                return
            if self._active_requests[url] >= self._max_concurrent_requests:
                self.set_replica_unavailable_no_lock(url)

    async def post_execute_hook(self, url: str,
                                request: fastapi.Request) -> None:
        async with self._lock:
            await self._load_balancing_policy.post_execute_hook(url, request)
            if url in self._active_requests and self._active_requests[url] > 0:
                self._active_requests[url] -= 1
                if self._use_ie_queue_indicator:
                    return
                if self._active_requests[url] < self._max_concurrent_requests:
                    self.set_replica_available_no_lock(url)


class SkyServeLoadBalancer:
    """SkyServeLoadBalancer: distribute incoming traffic with proxy.

    This class accept any traffic to the controller and proxies it
    to the appropriate endpoint replica according to the load balancing
    policy.
    """

    def __init__(
        self,
        controller_url: str,
        load_balancer_port: int,
        load_balancing_policy_name: Optional[str] = None,
        meta_load_balancing_policy_name: Optional[str] = None,
        region: Optional[str] = None,
        tls_credential: Optional[serve_utils.TLSCredential] = None,
        max_concurrent_requests: int = 10,
        is_local_debug_mode: bool = False,
        use_ie_queue_indicator: bool = True,
    ) -> None:
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
            use_ie_queue_indicator: Whether to use "whether the inference
                engine queue is full or not" as an indicator for available
                replicas. Defaults to True.
        """
        self._url: Optional[str] = None
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
                                                    max_concurrent_requests,
                                                    use_ie_queue_indicator)
        self._lb_pool: ClientPool = ClientPool(meta_load_balancing_policy_name,
                                               max_concurrent_requests,
                                               use_ie_queue_indicator,
                                               enable_latency_check=True)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._workload_steal_session: Optional[aiohttp.ClientSession] = None
        self._ie_queue_probe_session: Optional[aiohttp.ClientSession] = None
        self._lb_pool_probe_session: Optional[aiohttp.ClientSession] = None
        self._request_queue: Optional[QueueWithLock[RequestQueueEntry]] = None
        self._tasks: List[asyncio.Task] = []
        self._external_host: str = serve_utils.get_external_host()
        self._handle_request_tasks: List[asyncio.Task] = []
        # Mapping from LB URL to a list of number of requests to be stolen.
        # Each entry in the list represents a round of stealing. We put a
        # corresponding barrier in the request queue for each round. After the
        # barrier, any un-stolen requests will be discarded.
        self._lb_to_steal_requests: Dict[
            str, List[int]] = collections.defaultdict(list)
        self._lb_to_last_steal_time: Dict[str, float] = collections.defaultdict(
            float)
        self._steal_requests_lock: asyncio.Lock = asyncio.Lock()
        self._latest_req_id: int = 0
        self._use_ie_queue_indicator: bool = use_ie_queue_indicator
        # self._steal_targets_cache: Optional[List[str]] = None
        # self._self_url_cache: Optional[str] = None
        # TODO(tian): Temporary debugging solution. Remove this in production.
        self._replica2id: Dict[str, str] = {}
        self._lb2region: Dict[str, str] = {}
        self._is_local_debug_mode = is_local_debug_mode
        self._steal_targets_cnt: int = 0
        self._lb_to_queue_size: Dict[str, int] = {}

    # async def _lbs_with_steal_requests(self) -> List[str]:
    #     """Return the LBs that have requests to steal."""
    #     async with self._steal_requests_lock:
    #         return [
    #             lb for lb, num_steals in self._lb_to_steal_requests.items()
    #             if num_steals
    #         ]

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
                    if self._region is not None and self._region != 'global':
                        ready_urls = ready_replica_urls.get(self._region, [])
                    else:
                        ready_urls = sum(ready_replica_urls.values(), [])
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
                    # await self._lb_pool.set_all_replicas_unavailable_except(
                    #     await self._lbs_with_steal_requests())
                    # async with self._steal_requests_lock:
                    #     available_lbs = (
                    #         await self._lb_pool.available_replicas())
                    #     logger.info(f'LBs with steal requests: '
                    #                 f'{self._lb_to_steal_requests}, '
                    #                 f'Available LBs: {available_lbs}')

            await asyncio.sleep(constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS)
            # Await those tasks after the interval to avoid blocking.
            await asyncio.gather(*close_client_tasks)

    async def _proxy_request_to(self, pool_to_use: ClientPool,
                                proxy_request: httpx.Request,
                                client: httpx.AsyncClient, url: str,
                                request: fastapi.Request,
                                extra_header: Dict[str, str],
                                is_from_lb: bool) -> fastapi.responses.Response:
        """Proxy the request to the specified URL.

        Returns:
            The response from the endpoint replica. Return the exception
            encountered if anything goes wrong.
        """
        logger.info(f'Proxy request to {url}')
        proxy_response = await client.send(proxy_request, stream=True)

        async def background_func():
            await proxy_response.aclose()
            await pool_to_use.post_execute_hook(url, request)
            # Post execute hook for self LB here.
            if not is_from_lb and self._url is not None and not _LB_PUSHING_ENABLE_LB:
                await self._lb_pool.post_execute_hook(self._url, request)

        decision_id = len(proxy_response.headers)
        extra_header = {(f'{k}-{decision_id}' if 'decision' in k else k): v
                        for k, v in extra_header.items()}

        proxy_response.headers.update(extra_header)
        return fastapi.responses.StreamingResponse(
            content=proxy_response.aiter_raw(),
            status_code=proxy_response.status_code,
            headers=proxy_response.headers,
            background=background.BackgroundTask(background_func))

    async def _request_finish_callback(self, pool_to_use: ClientPool,
                                       proxy_request: httpx.Request,
                                       client: httpx.AsyncClient, url: str,
                                       entry: RequestEntry,
                                       extra_header: Dict[str, str],
                                       is_from_lb: bool) -> None:
        try:
            response = await self._proxy_request_to(pool_to_use, proxy_request,
                                                    client, url, entry.request,
                                                    extra_header, is_from_lb)
            entry.response_future.set_result(response)
            entry.request_event.set()
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error when proxy request to {url}: '
                         f'{common_utils.format_exception(e)}\n'
                         f'  Traceback: {traceback.format_exc()}')
            entry.set_failed_on(e)

    async def _handle_requests(self, url: str, entry: RequestEntry,
                               is_from_lb: bool) -> None:
        """Handle the request."""
        assert self._loop is not None
        try:
            entry.time_scheduled = time.time()
            pool_to_use = self._lb_pool if is_from_lb else self._replica_pool
            await pool_to_use.pre_execute_hook(url, entry.request)
            # Record the cache for self LB here.
            if not is_from_lb and self._url is not None and not _LB_PUSHING_ENABLE_LB:
                await self._lb_pool.pre_execute_hook(self._url, entry.request)
            # We defer the get of the client here on purpose, for case when the
            # replica is ready in `_put_request_to_queue` but refreshed before
            # entering this function. In that case we will return an error here
            # and retry to find next ready replica. We also need to wait for the
            # update of the client pool to finish before getting the client.
            client = await pool_to_use.get_client(url)
            if client is None:
                entry.response_future.set_exception(
                    RuntimeError(f'Client for {url} not found.'))
                entry.request_event.set()
                return
            worker_url = httpx.URL(
                path=entry.request.url.path,
                query=entry.request.url.query.encode('utf-8'))
            headers = entry.request.headers.mutablecopy()
            if is_from_lb:
                # If it is not from LB, then the following request will be sent
                # out from LB. So we add the header to indicate it.
                headers[_IS_FROM_LB_HEADER] = 'true'
                headers[_QUEUE_SIZE_HEADER] = str(
                    self._lb_to_queue_size.get(url, -1))
            proxy_request = client.build_request(
                entry.request.method,
                worker_url,
                headers=headers.raw,
                content=await entry.request.body(),
                timeout=constants.LB_STREAM_TIMEOUT)
            # NOTE(tian): The first and second hop is inversed here.
            if not is_from_lb:
                prefix = 'first-hop-'
            else:
                prefix = 'second-hop-'
            extra_header = {
                f'{prefix}sky-time-arrive': str(entry.time_arrive),
                f'{prefix}sky-time-scheduled': str(entry.time_scheduled),
            }
            if not is_from_lb:
                replica_id = self._replica2id.get(url, 'N/A')
                extra_header['replica-decision'] = (
                    f'Select Replica with id {replica_id} ({url})')
            else:
                lb_region = self._lb2region.get(url, 'N/A')
                extra_header['lb-decision'] = (
                    f'Select LB in region {lb_region} ({url})')
            coro = self._request_finish_callback(pool_to_use, proxy_request,
                                                 client, url, entry,
                                                 extra_header, is_from_lb)
            self._handle_request_tasks.append(self._loop.create_task(coro))
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f'Error when proxy request to {url}: '
                         f'{common_utils.format_exception(e)}')
            return e

    async def _steal_targets(self) -> List[str]:
        """Return the target LBs to steal from."""
        self._steal_targets_cnt += 1
        st = time.perf_counter()
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
        if not steal_targets:
            return steal_targets
        latencies_tasks = [
            self._lb_pool.get_latency(lb) for lb in steal_targets
        ]
        latencies = await asyncio.gather(*latencies_tasks)
        if any(lat is None for lat in latencies):
            return steal_targets
        # Sort steal_targets based on latencies
        steal_targets_with_latencies = [
            (target, lat) for target, lat in zip(steal_targets, latencies)
        ]
        steal_targets_with_latencies.sort(key=lambda x: x[1])
        steal_targets = [target for target, _ in steal_targets_with_latencies]
        if self._steal_targets_cnt % 1000 == 0:
            elapsed = time.perf_counter() - st
            logger.info(f'[{elapsed:.4f}s] '
                        'Steal targets with latencies: '
                        f'{steal_targets_with_latencies}, '
                        f'Steal targets: {steal_targets}, '
                        f'Self URL: {self_url}, '
                        f'External Host: {self._external_host}')
        # self._steal_targets_cache = steal_targets
        # self._self_url_cache = self_url
        if self._url is None:
            self._url = self_url
        else:
            assert self._url == self_url
        return steal_targets

    async def _queue_processor(self) -> None:
        """Background task to process queued requests."""
        assert self._loop is not None
        assert self._request_queue is not None
        logger.info('Starting request queue processor')
        while True:
            await asyncio.sleep(_QUEUE_PROCESSOR_SLEEP_TIME)
            async with self._steal_requests_lock:
                try:
                    if await self._request_queue.empty():
                        continue
                    # logger.info('Length of request queue for '
                    #             f'{self._external_host}: '
                    #             f'{await self._request_queue.qsize()}')

                    # Peek at the first item in the queue without removing it
                    entry: RequestQueueEntry = await self._request_queue.get_and_remove(
                    )
                    assert isinstance(entry, RequestEntry)

                    if (_LB_PUSHING_ENABLE_LB and _DO_PUSHING_ACROSS_LB and
                            not entry.request.headers.get(
                                _IS_FROM_LB_HEADER, False)):
                        # is not from lb. forward to lb first.
                        try:
                            ready_lb_url = await self._lb_pool.select_replica(
                                entry.request)
                        except starlette_requests.ClientDisconnect as e:
                            # Client disconnected. Skip this request.
                            entry.set_failed_on(e)
                            continue

                        # We can forward to self.
                        # if ready_lb_url is not None and ready_lb_url != self._url:
                        if ready_lb_url is not None:
                            # Process the request if a LB is available
                            # Now we can safely remove it from the queue
                            try:
                                logger.info(
                                    f'Processing queued request {entry.request.url} '
                                    f'from User to LB {ready_lb_url}.')
                                await self._handle_requests(ready_lb_url,
                                                            entry,
                                                            is_from_lb=True)
                            except Exception as e:  # pylint: disable=broad-except
                                logger.error(
                                    f'Error when processing queued request '
                                    f'to LB {ready_lb_url}: '
                                    f'{common_utils.format_exception(e)}\n'
                                    f'  Traceback: {traceback.format_exc()}')
                                # Set exception to propagate to the waiting handler
                                entry.set_failed_on(e)
                            continue

                    # TODO(tian): In this no replica case, if there is any replica
                    # in other LB region, we should let them steal instead of
                    # returning 503.
                    if await self._replica_pool.empty():
                        exception = fastapi.HTTPException(
                            # 503 means that the server is currently
                            # unable to handle the incoming requests.
                            status_code=503,
                            detail=('No ready replicas. '
                                    'Use "sky serve status [SERVICE_NAME]" '
                                    'to check the replica status.'))
                        entry.response_future.set_exception(exception)
                        entry.request_event.set()
                        continue

                    # Either because of there is no LBs stealing requests, or
                    # because the match rate is high so the local region is
                    # selected, we attempt to find an available replica here.
                    try:
                        ready_replica_url = await self._replica_pool.select_replica(
                            entry.request)
                    except starlette_requests.ClientDisconnect as e:
                        # Client disconnected. Skip this request.
                        entry.set_failed_on(e)
                        continue

                    if ready_replica_url is not None:
                        # Process the request if a replica is available
                        # Now we can safely remove it from the queue
                        try:
                            logger.info(
                                f'Processing queued request {entry.request.url} '
                                f'from User to Replica {ready_replica_url}.')
                            await self._handle_requests(ready_replica_url,
                                                        entry,
                                                        is_from_lb=False)
                        except Exception as e:  # pylint: disable=broad-except
                            logger.error(
                                f'Error when processing queued request '
                                f'to Replica {ready_replica_url}: '
                                f'{common_utils.format_exception(e)}\n'
                                f'  Traceback: {traceback.format_exc()}')
                            # Set exception to propagate to the waiting handler
                            entry.set_failed_on(e)
                        continue

                    # If not enabled, skip.
                    if not _DO_PUSHING_ACROSS_LB or _LB_PUSHING_ENABLE_LB:
                        continue
                    # If the request is already from another LB, avoid pushing
                    # it elsewhere. This should not happen since we only push
                    # to available LBs. But it might happen on staleness issue
                    # so we wait for next round.
                    # TODO(tian): Maybe we should have 2 queues: one for requests
                    # from users and one for requests from other LBs.
                    if entry.request.headers.get(_IS_FROM_LB_HEADER, False):
                        continue

                    logger.info(
                        f'self._url: {self._url}, Queue Size: '
                        f'{entry.request.headers.get(_QUEUE_SIZE_HEADER, 0)}, '
                        'self actual queue size: '
                        f'{await self._request_queue.actual_size()}')

                    # If enabled and when local replica all unavailable,
                    # try push it to other LBs.
                    try:
                        ready_lb_url = await self._lb_pool.select_replica(
                            entry.request)
                    except starlette_requests.ClientDisconnect as e:
                        # Client disconnected. Skip this request.
                        entry.set_failed_on(e)
                        continue

                    # Don't forward to self.
                    if ready_lb_url is not None and ready_lb_url != self._url:
                        # Process the request if a LB is available
                        # Now we can safely remove it from the queue
                        try:
                            logger.info(
                                f'Processing queued request {entry.request.url} '
                                f'from User to LB {ready_lb_url}.')
                            await self._handle_requests(ready_lb_url,
                                                        entry,
                                                        is_from_lb=True)
                        except Exception as e:  # pylint: disable=broad-except
                            logger.error(
                                f'Error when processing queued request '
                                f'to LB {ready_lb_url}: '
                                f'{common_utils.format_exception(e)}\n'
                                f'  Traceback: {traceback.format_exc()}')
                            # Set exception to propagate to the waiting handler
                            entry.set_failed_on(e)
                        continue

                    # Put it back if not successfully scheduled.
                    await self._request_queue.put_in_head(entry)

                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f'Error in queue processor: '
                                 f'{common_utils.format_exception(e)}\n'
                                 f'  Traceback: {traceback.format_exc()}')

    async def _put_request_to_queue(
            self, request: fastapi.Request) -> fastapi.responses.Response:
        """Queue the request for processing by the queue processor."""
        assert self._request_queue is not None
        self._request_aggregator.add(request)
        logger.info(f'Received request {request.url}, my url {self._url}.')

        try:
            # Create future and event in the current event loop context
            assert self._loop is not None
            response_future: asyncio.Future[
                fastapi.responses.Response] = self._loop.create_future()
            request_event: asyncio.Event = asyncio.Event()
            entry = RequestEntry(self._latest_req_id, time.time(), None,
                                 request, request_event, response_future)
            self._latest_req_id += 1

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
                         f'{common_utils.format_exception(e)}\n'
                         f'  Traceback: {traceback.format_exc()}')
            raise fastapi.HTTPException(
                status_code=500, detail=f'Error processing request: {str(e)}')

    async def _health_check(self) -> fastapi.responses.Response:
        """Health check endpoint."""
        return fastapi.responses.Response(status_code=200)

    async def _configuration(self) -> fastapi.responses.Response:
        """Return the configuration of the load balancer."""
        assert self._request_queue is not None
        queue_size = await self._request_queue.qsize()
        logger.info(
            f'Check LB configuration: {self._url}, queue_size: {queue_size}')
        return fastapi.responses.JSONResponse(
            status_code=200,
            content={
                'queue_size': queue_size,
                'queue_size_actual': await self._request_queue.actual_size(),
                'replica_queue': await self._replica_pool.active_requests(),
                'num_replicas': len(self._replica_pool.ready_replicas()),
                'num_available_replicas': len(
                    await self._replica_pool.available_replicas()),
                'max_concurrent_requests': self._max_concurrent_requests,
                'load_balancing_policy_name': self._load_balancing_policy_name
            })

    async def _cleanup_completed_tasks(self) -> None:
        """Cleanup completed tasks."""
        while True:
            for task in self._handle_request_tasks:
                if task.done():
                    self._handle_request_tasks.remove(task)
            await asyncio.sleep(_QUEUE_PROCESSOR_SLEEP_TIME)

    async def _req_check_conf(self, target: str) -> LBConfigEntry:
        assert self._workload_steal_session is not None
        # TODO(tian): Use urlparse for robustness.
        # TODO(tian): Investigate this pylint warning.
        async with self._workload_steal_session.get(  # pylint: disable=not-async-context-manager
            target + '/conf') as response:
            conf = await response.json()
            return LBConfigEntry(conf['queue_size'], conf['queue_size_actual'],
                                 conf['num_replicas'],
                                 sum(conf['replica_queue'].values()))

    async def _req_steal_request(self, target: str, num_steal: int) -> int:
        """Issue a steal request to the target LB.

        Returns the actual number of requests stolen.
        """
        assert self._workload_steal_session is not None
        async with self._workload_steal_session.post(  # pylint: disable=not-async-context-manager
                target + '/steal-request',
                json={
                    'num_steal': num_steal,
                    'source_lb_url': self._url,
                }) as steal_response:
            if steal_response.status != 200:
                logger.error(f'Error in request stealing: '
                             f'{await steal_response.text()}')
                return 0
            remaining_to_steal = (await
                                  steal_response.json())['remaining_to_steal']
            actual_num_steal = num_steal - remaining_to_steal
            logger.info(f'Actual steal: '
                        f'{actual_num_steal}/{num_steal} '
                        f'from {target}.')
            return actual_num_steal

    async def _request_stealing_v1_avg(self, steal_targets: List[str],
                                       self_qsize: int) -> None:
        """Steal requests from other LBs to balance the load.

        This assumes all replicas should handle the same amount of requests.
        """
        conf_tasks = [self._req_check_conf(target) for target in steal_targets]
        conf_results = await asyncio.gather(*conf_tasks)
        self_replica_queue_size_total = sum(
            (await self._replica_pool.active_requests()).values())
        total_queue_size = (
            sum(result.queue_size + result.replica_queue_size_total
                for result in conf_results) + self_qsize +
            self_replica_queue_size_total)
        if total_queue_size <= 0:
            return
        self_num_replicas = len(self._replica_pool.ready_replicas())
        total_num_replicas = sum(
            result.num_replicas for result in conf_results) + self_num_replicas
        if total_num_replicas <= 0:
            return
        per_replica_num_reqs = total_queue_size // total_num_replicas
        self_num_reqs = per_replica_num_reqs * self_num_replicas
        if self_num_reqs <= 0:
            return
        self_num_reqs_remaining = (self_num_reqs - self_qsize -
                                   self_replica_queue_size_total)
        logger.info(f'total_queue_size: {total_queue_size}, '
                    'self_replica_queue_size_total: '
                    f'{self_replica_queue_size_total}, '
                    f'total_num_replicas: {total_num_replicas}, '
                    f'self_num_replicas: {self_num_replicas}, '
                    f'self_num_reqs: {self_num_reqs}, '
                    f'self_num_reqs_remaining: {self_num_reqs_remaining}')
        tasks = []
        for target, conf_result in zip(steal_targets, conf_results):
            if self_num_reqs_remaining <= 0:
                break
            if (conf_result.queue_size <= 0 or
                    conf_result.queue_size_actual <= 0):
                continue
            # Steal until the target reaches the avg number of reqs per replica.
            # Dont steal more than the actual queue size.
            should_steal = (conf_result.queue_size +
                            conf_result.replica_queue_size_total -
                            per_replica_num_reqs * conf_result.num_replicas)
            num_steal = min(self_num_reqs_remaining, should_steal,
                            conf_result.queue_size_actual)
            if num_steal <= 0:
                continue
            self_num_reqs_remaining -= num_steal
            logger.info(f'Steal from {target} with {num_steal} requests; '
                        f'queue_size: {conf_result.queue_size}, '
                        f'queue_size_actual: {conf_result.queue_size_actual}, '
                        f'should_steal: {should_steal}, '
                        f'self_num_reqs_remaining: {self_num_reqs_remaining}.')
            tasks.append(
                asyncio.create_task(self._req_steal_request(target, num_steal)))
        await asyncio.gather(*tasks)

    async def _request_stealing_v2_small(self, steal_targets: List[str],
                                         num_available_replicas: int) -> None:
        """Steal requests from other LBs to balance the load.

        This only steals small amount of requests from other LBs every time.
        """
        num_to_steal = num_available_replicas * 3
        for target in steal_targets:
            actual_num_to_steal = await self._req_steal_request(
                target, num_to_steal)
            num_to_steal -= actual_num_to_steal
            if num_to_steal <= 0:
                break

    async def _request_stealing_loop(self) -> None:
        """Background task to process request stealing."""
        assert self._loop is not None
        assert self._workload_steal_session is not None
        assert self._request_queue is not None
        logger.info('Starting request stealing loop')
        while True:
            await asyncio.sleep(_QUEUE_PROCESSOR_SLEEP_TIME)
            try:
                # Refresh the steal targets latency even if there is no stealing.
                steal_targets = await self._steal_targets()
                self_qsize = await self._request_queue.qsize()
                if self_qsize > _STEAL_TRIGGER_THRESHOLD:
                    continue
                num_available_replicas = len(
                    await self._replica_pool.available_replicas())
                # Don't steal if there is no available replica.
                if num_available_replicas <= 0:
                    continue
                # It is possible that self_url is not ready in the LB
                # replica manager yet. We wait until it is ready.
                if not steal_targets or self._url is None:
                    continue
                if _USE_V2_STEALING:
                    await self._request_stealing_v2_small(
                        steal_targets, num_available_replicas)
                else:
                    await self._request_stealing_v1_avg(steal_targets,
                                                        self_qsize)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in request stealing loop: '
                             f'{common_utils.format_exception(e)}\n'
                             f'  Traceback: {traceback.format_exc()}')

    async def _steal_request(
            self, steal_request: fastapi.Request) -> fastapi.responses.Response:
        """Let other LBs steal requests from this LB."""
        # assert self._loop is not None
        assert self._request_queue is not None
        req_json = await steal_request.json()
        num_steal = req_json['num_steal']
        source_lb_url = req_json['source_lb_url']
        if num_steal <= 0:
            return fastapi.responses.JSONResponse(
                status_code=200, content={'remaining_to_steal': 0})
        logger.info(f'Received steal request from {source_lb_url} with '
                    f'{num_steal} requests.')
        # Option 1. Return immediately and let the queue processor handle it.
        # async with self._steal_requests_lock:
        #     # The last steal time will be initialized to 0.0 if the key does
        #     # not exist. This guarantees that the first steal request will be
        #     # honored and the remaining_to_steal will be set to 0.
        #     interval = time.time() - self._lb_to_last_steal_time[source_lb_url]
        #     if interval > _MIN_STEAL_INTERVAL:
        #         self._lb_to_steal_requests[source_lb_url].append(num_steal)
        #         self._lb_to_last_steal_time[source_lb_url] = time.time()
        #         await self._lb_pool.set_replica_available(source_lb_url)
        #         remaining_to_steal = 0
        #         logger.info(f'{source_lb_url} steals {num_steal} requests. '
        #                     f'Current steal requests: '
        #                     f'{self._lb_to_steal_requests}, '
        #                     f'Last steal time: '
        #                     f'{self._lb_to_last_steal_time}')
        #         await self._request_queue.put(StealBarrierEntry(source_lb_url))
        #     else:
        #         # logger.info(f'Not stealing requests from {source_lb_url} '
        #         #             f'because of the minimum steal interval '
        #         #             f'({interval}/{_MIN_STEAL_INTERVAL}). '
        #         #             'Last steal time: '
        #         #             f'{self._lb_to_last_steal_time[source_lb_url]}')
        #         remaining_to_steal = num_steal
        # return fastapi.responses.JSONResponse(
        #     status_code=200, content={'remaining_to_steal': remaining_to_steal})

        # Option 2 (legacy implementation): Directly steal requests.
        # async with self._steal_requests_lock:
        #     for i in range(await self._request_queue.qsize()):
        #         # Re-getting the queue size since it is possible the queue changed
        #         # during the loop, e.g. a request from head is popped out.
        #         idx = await self._request_queue.qsize() - 1 - i
        #         if idx < 0:
        #             break
        #         entry = await self._request_queue.get(idx)
        #         request, _, _ = entry
        #         # Check if the request is from the source LB.
        #         if request.headers.get(_IS_FROM_LB_HEADER, False):
        #             continue
        #         await self._request_queue.get_and_remove(idx)
        #         await self._handle_requests(source_lb_url, entry, is_from_lb=True)
        #         num_steal -= 1
        #         if not num_steal:
        #             break
        #     if num_steal != 0:
        #         logger.info(f'{num_steal} requests was NOT stolen and remained '
        #                     f'from {source_lb_url}.')

        #     # if num_steal:
        #     #     raise fastapi.HTTPException(
        #     #         status_code=429,
        #     #         detail=f'Not enough requests to steal. '
        #     #         f'Requested {num_steal}, but only {num_steal} requests '
        #     #         f'available.')
        #     return fastapi.responses.JSONResponse(
        #         status_code=200, content={'remaining_to_steal': num_steal})

        # Option 3. Steal with lock and sorted based on prefix match rate.
        start_time = time.perf_counter()
        async with self._steal_requests_lock:
            steal_entries: List[StealEntry] = []
            # First part: select LB replica and calculate match rates
            select_lb_start_time = time.perf_counter()
            i = 0
            while i < await self._request_queue.qsize():
                entry = await self._request_queue.get(i)
                i += 1
                if isinstance(entry, StealBarrierEntry):
                    continue
                # Check if the request is from the source LB.
                if entry.request.headers.get(_IS_FROM_LB_HEADER, False):
                    continue
                try:
                    result = await self._lb_pool.select_replica(
                        entry.request, return_matched_rate=True)
                except starlette_requests.ClientDisconnect as e:
                    i -= 1
                    await self._request_queue.get_and_remove(i)
                    entry.set_failed_on(e)
                    continue
                if isinstance(result, tuple):
                    lb, matched_rate, matched_length = result
                else:
                    lb = result
                    matched_rate = -1.0
                    matched_length = -1
                steal_entry = StealEntry(entry.id, matched_rate, matched_length,
                                         lb == source_lb_url, i)
                steal_entries.append(steal_entry)

            logger.info(
                f'[time={time.time():.4f}] '
                f'num steal entries: {len(steal_entries)}, '
                f'num_steal: {num_steal}, '
                f'actual_size: {await self._request_queue.actual_size()}')

            select_lb_end_time = time.perf_counter()

            # Second part: sort entries and process stealing
            sort_start_time = time.perf_counter()
            steal_entries.sort()
            ids_to_steal = {
                steal_entry.id for steal_entry in steal_entries[:num_steal]
            }
            remaining_to_steal = num_steal - len(ids_to_steal)
            idx = 0
            while idx < await self._request_queue.qsize():
                entry = await self._request_queue.get(idx)
                if isinstance(entry, StealBarrierEntry):
                    continue
                if entry.id in ids_to_steal:
                    await self._request_queue.get_and_remove(idx)
                    await self._handle_requests(source_lb_url,
                                                entry,
                                                is_from_lb=True)
                else:
                    idx += 1
            sort_end_time = time.perf_counter()

            end_time = time.perf_counter()
            if remaining_to_steal > 0:
                logger.info(f'{remaining_to_steal} requests was NOT '
                            f'stolen and remained from {source_lb_url}.')
            t_select_lb = select_lb_end_time - select_lb_start_time
            t_sort = sort_end_time - sort_start_time
            logger.info(f'Time to select LB: {t_select_lb:.4f}s, '
                        f'Time to sort (len={len(steal_entries)}) '
                        f'and process: {t_sort:.4f}s, '
                        f'Total time: {end_time - start_time:.4f}s.')
            return fastapi.responses.JSONResponse(
                status_code=200,
                content={'remaining_to_steal': remaining_to_steal})

    async def _probe_ie_queue_one_replica(
            self, replica: str) -> Tuple[Optional[float], str]:
        """Probe the inference engine queue of one replica."""
        # TODO(tian): Support other inference engines.
        assert self._ie_queue_probe_session is not None
        try:
            # TODO(tian): Use urlparse for robustness, and change the session
            # name since this is no longer only used for stealing requests.
            async with self._ie_queue_probe_session.get(  # pylint: disable=not-async-context-manager
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
                         f'{common_utils.format_exception(e)}\n'
                         f'  Traceback: {traceback.format_exc()}')
        return None, replica

    async def _probe_ie_queue(self) -> None:
        """Probe the inference engine queue."""
        # Initially, immediately probe the inference engine queue.
        time_take_this_round = float(_IE_QUEUE_PROBE_INTERVAL)
        cnt = 0
        while True:
            cnt += 1
            await asyncio.sleep(
                max(0, _IE_QUEUE_PROBE_INTERVAL - time_take_this_round))
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
                        await self._replica_pool.set_replica_unavailable(replica
                                                                        )
                    else:
                        await self._replica_pool.set_replica_available(replica)
            time_end_set_replica = time.perf_counter()
            time_elapsed_set_replica = (time_end_set_replica -
                                        time_start_set_replica)
            if cnt % 30 == 0:
                logger.info(f'Probe time: {time_end_probe - time_start}s, '
                            f'Set replica time: {time_elapsed_set_replica}s.')
            time_take_this_round = time.perf_counter() - time_start

    async def _probe_lb_status_one_lb(
            self, lb: str, self_qsize: int) -> Tuple[Optional[bool], str]:
        """Probe the status of one load balancer.

        Returns: Whether the LB is available, and the LB URL."""
        assert self._lb_pool_probe_session is not None
        if lb == self._url:
            # Don't forward request to self.
            return False, lb
        try:
            async with self._lb_pool_probe_session.get(  # pylint: disable=not-async-context-manager
                    lb + '/conf') as response:
                conf = await response.json()
                # if its queue size is less than self's, it's available
                lb_available = (conf['queue_size'] <= self_qsize and
                                conf['queue_size'] <= _STEAL_TRIGGER_THRESHOLD
                                and conf['num_available_replicas'] > 0)
                logger.info(f'{lb} queue size: {conf["queue_size"]}, '
                            f'self_qsize: {self_qsize}')
                self._lb_to_queue_size[lb] = conf['queue_size']
                return lb_available, lb
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error probing load balancer status of {lb}: '
                         f'{common_utils.format_exception(e)}\n'
                         f'  Traceback: {traceback.format_exc()}')
            return None, lb

    async def _probe_lb_status(self) -> None:
        """Probe the status of the load balancer."""
        assert self._request_queue is not None
        # Initially, immediately probe the load balancer status.
        time_take_this_round = float(_IE_QUEUE_PROBE_INTERVAL)
        while True:
            await asyncio.sleep(
                max(0, _IE_QUEUE_PROBE_INTERVAL - time_take_this_round))
            st = time.perf_counter()
            # Use this to refresh the self url
            await self._steal_targets()
            self_qsize = await self._request_queue.qsize()
            tasks = []
            for lb in self._lb_pool.ready_replicas():
                tasks.append(self._probe_lb_status_one_lb(lb, self_qsize))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for lb_available, lb in results:
                if isinstance(lb_available, Exception):
                    logger.error(
                        f'Error probing load balancer status: '
                        f'{common_utils.format_exception(lb_available)}')
                elif lb_available is None:
                    logger.error(f'No load balancer status found for {lb}.')
                else:
                    if not lb_available:
                        await self._lb_pool.set_replica_unavailable(lb)
                    else:
                        await self._lb_pool.set_replica_available(lb)
            time_take_this_round = time.perf_counter() - st
            logger.info(f'Probe lb one round takes {time_take_this_round:.4f}s')

    def run(self):
        # Add health check endpoint first so it takes precedence
        self._app.add_api_route(constants.LB_HEALTH_ENDPOINT,
                                self._health_check,
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
                handler.addFilter(ConfFilter())
            # # Make sure we're using the current event loop
            self._loop = asyncio.get_running_loop()
            self._workload_steal_session = aiohttp.ClientSession()
            self._ie_queue_probe_session = aiohttp.ClientSession()
            self._lb_pool_probe_session = aiohttp.ClientSession()
            self._request_queue = QueueWithLock()

            if _DO_PUSHING_ACROSS_LB:
                if _LB_PUSHING_ENABLE_LB:
                    self._lb_pool.enable_load_balancing()
                else:
                    self._tasks.append(
                        self._loop.create_task(self._probe_lb_status()))
            if _DO_PUSHING_TO_REPLICA:
                self._replica_pool.enable_load_balancing()
            else:
                if self._use_ie_queue_indicator and _ENABLE_SELECTIVE_PUSHING:
                    self._tasks.append(
                        self._loop.create_task(self._probe_ie_queue()))

            await self._lb_pool.re_init_lock()
            await self._replica_pool.re_init_lock()

            # Register controller synchronization task
            self._tasks.append(
                self._loop.create_task(self._sync_with_controller()))

            # Start the request queue processor
            self._tasks.append(self._loop.create_task(self._queue_processor()))

            # Start the task to cleanup completed tasks
            self._tasks.append(
                self._loop.create_task(self._cleanup_completed_tasks()))

            # Start the request stealing loop. Only enable if the region is set,
            # i.e. not global LB; and pushing across LBs is not enabled.
            if (self._region is not None and not _DO_PUSHING_ACROSS_LB and
                    not _FORCE_DISABLE_STEALING):
                self._tasks.append(
                    self._loop.create_task(self._request_stealing_loop()))

            self._tasks.append(
                self._loop.create_task(self._replica_pool.background_task()))

            self._tasks.append(
                self._loop.create_task(self._lb_pool.background_task()))

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
        logger.info('Started lb in version lock-queue-fixed-queue-size.')
        logger.info(
            f'===============System Config===============\n'
            f'_DO_PUSHING_ACROSS_LB: [{_DO_PUSHING_ACROSS_LB}], '
            f'[{os.getenv(env_options.Options.DO_PUSHING_ACROSS_LB.env_key)}], '
            f'_DO_PUSHING_TO_REPLICA: {_DO_PUSHING_TO_REPLICA}, '
            f'[{os.getenv(env_options.Options.DO_PUSHING_TO_REPLICA.env_key)}], '
            f'_LB_PUSHING_ENABLE_LB: {_LB_PUSHING_ENABLE_LB}, '
            f'[{os.getenv(env_options.Options.LB_PUSHING_ENABLE_LB.env_key)}], '
            f'_ENABLE_SELECTIVE_PUSHING: {_ENABLE_SELECTIVE_PUSHING}, '
            f'[{os.getenv(env_options.Options.ENABLE_SELECTIVE_PUSHING.env_key)}], '
            f'_DISABLE_LEAST_LOAD_IN_PREFIX: {_DISABLE_LEAST_LOAD_IN_PREFIX}, '
            f'[{os.getenv(env_options.Options.DISABLE_LEAST_LOAD_IN_PREFIX.env_key)}], '
            f'_FORCE_DISABLE_STEALING: {_FORCE_DISABLE_STEALING}, '
            f'[{os.getenv(env_options.Options.FORCE_DISABLE_STEALING.env_key)}], '
            f'_USE_IE_QUEUE_INDICATOR: {_USE_IE_QUEUE_INDICATOR}, '
            f'[{os.getenv(env_options.Options.USE_IE_QUEUE_INDICATOR.env_key)}], '
            f'max_concurrent_requests: {self._max_concurrent_requests}, '
            f'use_ie_queue_indicator: {self._use_ie_queue_indicator}')
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
    use_ie_queue_indicator: Optional[bool] = None,
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
    """
    if use_ie_queue_indicator is None:
        use_ie_queue_indicator = _USE_IE_QUEUE_INDICATOR
    load_balancer = SkyServeLoadBalancer(
        controller_url=controller_addr,
        load_balancer_port=load_balancer_port,
        load_balancing_policy_name=load_balancing_policy_name,
        meta_load_balancing_policy_name=meta_load_balancing_policy_name,
        region=region,
        tls_credential=tls_credential,
        max_concurrent_requests=max_concurrent_requests,
        use_ie_queue_indicator=use_ie_queue_indicator,
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
                        default=None,
                        help='The region of the load balancer.')
    parser.add_argument('--max-concurrent-requests',
                        type=int,
                        default=10,
                        help='Maximum concurrent requests per replica.')
    args = parser.parse_args()
    run_load_balancer(
        args.controller_addr,
        args.load_balancer_port,
        args.load_balancing_policy,
        args.meta_load_balancing_policy,
        args.region,
        tls_credential=None,
        max_concurrent_requests=args.max_concurrent_requests,
    )

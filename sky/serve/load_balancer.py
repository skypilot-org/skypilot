"""LoadBalancer: Distribute any incoming request to all ready replicas."""
import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple, Union

import aiohttp
import fastapi
import httpx
from starlette import background
import uvicorn

from sky import sky_logging
from sky.serve import constants
from sky.serve import load_balancing_policies as lb_policies
from sky.serve import serve_utils
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)

# Add constants for timeouts and monitoring
FIRST_BYTE_TIMEOUT_SECONDS = 5.0  # Timeout for first byte detection
REQUEST_STEALING_INTERVAL_SECONDS = 2.0  # How often to check for stealing opportunities
MAX_STEAL_ATTEMPTS = 3  # Maximum number of attempts to steal a request


class ReplicaState:
    """Tracks the state of replicas including active and stalled requests."""

    def __init__(self):
        self._lock = threading.Lock()
        # Maps replica URL to set of request IDs
        self._replica_requests: Dict[str, Set[str]] = {}
        # Maps request ID to (replica URL, start time)
        self._request_info: Dict[str, Tuple[str, float]] = {}
        # Set of requests that have started streaming
        self._streaming_started: Set[str] = set()
        # Maps request ID to number of steal attempts
        self._steal_attempts: Dict[str, int] = {}

    def register_replica(self, replica_url: str):
        """Register a new replica."""
        with self._lock:
            if replica_url not in self._replica_requests:
                self._replica_requests[replica_url] = set()

    def unregister_replica(self, replica_url: str) -> Set[str]:
        """Unregister a replica and return its active requests."""
        with self._lock:
            active_requests = self._replica_requests.pop(replica_url, set())
            # Clean up request info for this replica
            for req_id in list(self._request_info.keys()):
                if self._request_info[req_id][0] == replica_url:
                    self._request_info.pop(req_id, None)
            return active_requests

    def add_request(self, request_id: str, replica_url: str):
        """Add a new request to a replica."""
        with self._lock:
            if replica_url in self._replica_requests:
                self._replica_requests[replica_url].add(request_id)
                self._request_info[request_id] = (replica_url, time.time())
                self._steal_attempts[request_id] = 0

    def remove_request(self, request_id: str):
        """Remove a request from tracking."""
        with self._lock:
            if request_id in self._request_info:
                replica_url, _ = self._request_info.pop(request_id)
                if replica_url in self._replica_requests:
                    self._replica_requests[replica_url].discard(request_id)
                self._streaming_started.discard(request_id)
                self._steal_attempts.pop(request_id, None)

    def mark_streaming_started(self, request_id: str):
        """Mark that a request has started streaming data."""
        with self._lock:
            if request_id in self._request_info:
                self._streaming_started.add(request_id)

    def get_replica_request_counts(self) -> Dict[str, int]:
        """Get the number of active requests for each replica."""
        with self._lock:
            return {
                replica: len(requests)
                for replica, requests in self._replica_requests.items()
            }

    def get_idle_replicas(self) -> List[str]:
        """Get replicas with no active requests."""
        with self._lock:
            return [
                replica for replica, requests in self._replica_requests.items()
                if not requests
            ]

    def get_stalled_requests(self) -> Dict[str, Tuple[str, float]]:
        """Get requests that haven't started streaming yet.
        
        Returns:
            Dict mapping request_id to (replica_url, wait_time)
        """
        with self._lock:
            current_time = time.time()
            return {
                req_id: (replica_url, current_time - start_time)
                for req_id, (replica_url,
                             start_time) in self._request_info.items()
                if req_id not in self._streaming_started and current_time -
                start_time > FIRST_BYTE_TIMEOUT_SECONDS and
                self._steal_attempts.get(req_id, 0) < MAX_STEAL_ATTEMPTS
            }

    def increment_steal_attempt(self, request_id: str):
        """Increment the steal attempt counter for a request."""
        with self._lock:
            if request_id in self._steal_attempts:
                self._steal_attempts[request_id] += 1

    def get_replica_url(self, request_id: str) -> Optional[str]:
        """Get the replica URL for a request."""
        with self._lock:
            if request_id in self._request_info:
                return self._request_info[request_id][0]
            return None


class FirstByteStreamWrapper:
    """Wrapper around response stream that detects first byte and handles cancellation."""

    def __init__(self, stream, request_id: str, replica_state: ReplicaState,
                 cancel_event: asyncio.Event,
                 load_balancing_policy: lb_policies.LoadBalancingPolicy,
                 request: fastapi.Request):
        self._stream = stream
        self._request_id = request_id
        self._replica_state = replica_state
        self._first_byte_received = False
        self._cancel_event = cancel_event
        self._load_balancing_policy = load_balancing_policy
        self._request = request
        self._replica_url = replica_state.get_replica_url(request_id)

    async def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            # Check for cancellation before getting next chunk
            if self._cancel_event.is_set():
                # Call post_execute_hook to update load balancing state
                if self._replica_url:
                    self._load_balancing_policy.post_execute_hook(
                        self._replica_url, self._request)
                logger.info(
                    f"Request {self._request_id} cancelled during streaming")
                raise StopAsyncIteration

            chunk = await self._stream.__anext__()
            if not self._first_byte_received and chunk:
                self._first_byte_received = True
                self._replica_state.mark_streaming_started(self._request_id)
            return chunk
        except StopAsyncIteration:
            raise


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
            tls_credential: Optional[serve_utils.TLSCredential] = None) -> None:
        """Initialize the load balancer.

        Args:
            controller_url: The URL of the controller.
            load_balancer_port: The port where the load balancer listens to.
            load_balancing_policy_name: The name of the load balancing policy
                to use. Defaults to None.
            tls_credentials: The TLS credentials for HTTPS endpoint. Defaults
                to None.
        """
        self._app = fastapi.FastAPI()
        self._controller_url: str = controller_url
        self._load_balancer_port: int = load_balancer_port
        # Use the registry to create the load balancing policy
        self._load_balancing_policy = lb_policies.LoadBalancingPolicy.make(
            load_balancing_policy_name)
        logger.info('Starting load balancer with policy '
                    f'{load_balancing_policy_name}.')
        self._request_aggregator: serve_utils.RequestsAggregator = (
            serve_utils.RequestTimestamp())
        self._tls_credential: Optional[serve_utils.TLSCredential] = (
            tls_credential)
        # TODO(tian): httpx.Client has a resource limit of 100 max connections
        # for each client. We should wait for feedback on the best max
        # connections.
        # Reference: https://www.python-httpx.org/advanced/resource-limits/
        #
        # If more than 100 requests are sent to the same replica, the
        # httpx.Client will queue the requests and send them when a
        # connection is available.
        # Reference: https://github.com/encode/httpcore/blob/a8f80980daaca98d556baea1783c5568775daadc/httpcore/_async/connection_pool.py#L69-L71 # pylint: disable=line-too-long
        # Enhanced state tracking for replicas and requests
        self._replica_state = ReplicaState()
        # Request ID counter
        self._request_counter = 0
        self._request_counter_lock = threading.Lock()
        # Request cancellation support
        self._request_cancel_events: Dict[str, asyncio.Event] = {}
        self._client_pool: Dict[str, httpx.AsyncClient] = dict()
        # We need this lock to avoid getting from the client pool while
        # updating it from _sync_with_controller.
        self._client_pool_lock: threading.Lock = threading.Lock()

    def _generate_request_id(self) -> str:
        """Generate a unique request ID."""
        with self._request_counter_lock:
            self._request_counter += 1
            return f"req-{self._request_counter}"

    async def _monitor_stalled_requests(self):
        """Monitor for requests that haven't started streaming and cancel them for retry."""
        while True:
            try:
                # Find stalled requests that can be retried
                stalled_requests = self._replica_state.get_stalled_requests()

                for req_id, (replica_url,
                             wait_time) in stalled_requests.items():
                    # Increment steal attempt counter
                    self._replica_state.increment_steal_attempt(req_id)

                    logger.info(
                        f"Cancelling stalled request {req_id} from {replica_url} "
                        f"after {wait_time:.2f}s wait for retry")

                    # Signal cancellation of the current request
                    cancel_event = self._request_cancel_events.get(req_id)
                    if cancel_event:
                        cancel_event.set()

            except Exception as e:
                logger.error(f"Error in stalled request monitor: {e}")

            await asyncio.sleep(REQUEST_STEALING_INTERVAL_SECONDS)

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
                            'ready_replica_urls', [])
                except aiohttp.ClientError as e:
                    logger.error('An error occurred when syncing with '
                                 f'the controller: {e}')
                else:
                    logger.info(f'Available Replica URLs: {ready_replica_urls}')
                    with self._client_pool_lock:
                        self._load_balancing_policy.set_ready_replicas(
                            ready_replica_urls)

                        # Register new replicas
                        for replica_url in ready_replica_urls:
                            self._replica_state.register_replica(replica_url)
                            if replica_url not in self._client_pool:
                                self._client_pool[replica_url] = (
                                    httpx.AsyncClient(base_url=replica_url))

                        # Handle removed replicas
                        urls_to_close = set(
                            self._client_pool.keys()) - set(ready_replica_urls)
                        client_to_close = []
                        for replica_url in urls_to_close:
                            # Get any active requests from the replica being removed
                            active_requests = self._replica_state.unregister_replica(
                                replica_url)
                            logger.warning(
                                f"Replica {replica_url} removed with "
                                f"{len(active_requests)} active requests")
                            # TODO: Handle active requests from removed replicas
                            assert False, "Remove replicas are not supported yet."
                            client_to_close.append(
                                self._client_pool.pop(replica_url))
                    for client in client_to_close:
                        close_client_tasks.append(client.aclose())

            await asyncio.sleep(constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS)
            # Await those tasks after the interval to avoid blocking.
            await asyncio.gather(*close_client_tasks)

    async def _proxy_request_to(
            self, url: str, request: fastapi.Request,
            request_id: str) -> Union[fastapi.responses.Response, Exception]:
        """Proxy the request to the specified URL.

        Args:
            url: The replica URL to proxy to
            request: The original request
            request_id: Unique identifier for this request

        Returns:
            The response from the endpoint replica. Return the exception
            encountered if anything goes wrong.
        """
        logger.info(f'Proxy request {request_id} to {url}')

        # Call pre-execute hook for load balancing policy
        self._load_balancing_policy.pre_execute_hook(url, request)

        # Register this request with the replica
        self._replica_state.add_request(request_id, url)

        # Create a cancellation event
        cancel_event = asyncio.Event()
        self._request_cancel_events[request_id] = cancel_event

        try:
            # We defer the get of the client here on purpose, for case when the
            # replica is ready in `_proxy_with_retries` but refreshed before
            # entering this function. In that case we will return an error here
            # and retry to find next ready replica. We also need to wait for the
            # update of the client pool to finish before getting the client.
            with self._client_pool_lock:
                client = self._client_pool.get(url, None)
            if client is None:
                # Clean up and call post-execute hook
                self._replica_state.remove_request(request_id)
                self._request_cancel_events.pop(request_id, None)
                self._load_balancing_policy.post_execute_hook(url, request)
                return RuntimeError(f'Client for {url} not found.')

            worker_url = httpx.URL(path=request.url.path,
                                   query=request.url.query.encode('utf-8'))
            proxy_request = client.build_request(
                request.method,
                worker_url,
                headers=request.headers.raw,
                content=await request.body(),
                timeout=constants.LB_STREAM_TIMEOUT)

            # Send the request
            try:
                proxy_response = await client.send(proxy_request, stream=True)
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                # Clean up and call post-execute hook on error
                self._replica_state.remove_request(request_id)
                self._request_cancel_events.pop(request_id, None)
                self._load_balancing_policy.post_execute_hook(url, request)
                logger.error(f'Error when proxy request {request_id} to {url}: '
                             f'{common_utils.format_exception(e)}')
                return e

            # Create a wrapper around the stream that detects the first byte
            # and also checks for cancellation during streaming
            wrapped_stream = FirstByteStreamWrapper(
                proxy_response.aiter_raw(), request_id, self._replica_state,
                cancel_event, self._load_balancing_policy, request)

            async def background_func():
                await proxy_response.aclose()
                # Clean up tracking information
                self._replica_state.remove_request(request_id)
                self._request_cancel_events.pop(request_id, None)

                # Note: We don't call post_execute_hook here because it's either:
                # 1. Already called in FirstByteStreamWrapper if cancelled
                # 2. Will be called normally when the stream completes

            return fastapi.responses.StreamingResponse(
                content=wrapped_stream,
                status_code=proxy_response.status_code,
                headers=proxy_response.headers,
                background=background.BackgroundTask(background_func))
        except Exception as e:
            # Clean up and call post-execute hook on any other error
            self._replica_state.remove_request(request_id)
            self._request_cancel_events.pop(request_id, None)
            self._load_balancing_policy.post_execute_hook(url, request)
            logger.error(
                f'Unexpected error when proxying request {request_id} to {url}: '
                f'{common_utils.format_exception(e)}')
            return e

    async def _proxy_with_retries(
            self, request: fastapi.Request) -> fastapi.responses.Response:
        """Try to proxy the request to the endpoint replica with retries."""
        request_id = self._generate_request_id()
        self._request_aggregator.add(request)
        # TODO(tian): Finetune backoff parameters.
        backoff = common_utils.Backoff(initial_backoff=1)
        # SkyServe supports serving on Spot Instances. To avoid preemptions
        # during request handling, we add a retry here.
        retry_cnt = 0
        while True:
            retry_cnt += 1
            with self._client_pool_lock:
                ready_replica_url = self._load_balancing_policy.select_replica(
                    request)
            if ready_replica_url is None:
                response_or_exception = fastapi.HTTPException(
                    # 503 means that the server is currently
                    # unable to handle the incoming requests.
                    status_code=503,
                    detail='No ready replicas. '
                    'Use "sky serve status [SERVICE_NAME]" '
                    'to check the replica status.')
            else:
                response_or_exception = await self._proxy_request_to(
                    ready_replica_url, request, request_id)
            if not isinstance(response_or_exception, Exception):
                return response_or_exception
            # When the user aborts the request during streaming, the request
            # will be disconnected. We do not need to retry for this case.
            if await request.is_disconnected():
                # 499 means a client terminates the connection
                # before the server is able to respond.
                return fastapi.responses.Response(status_code=499)

            # TODO(tian): Fail fast for errors like 404 not found.
            if retry_cnt == constants.LB_MAX_RETRY:
                if isinstance(response_or_exception, fastapi.HTTPException):
                    raise response_or_exception
                exception = common_utils.remove_color(
                    common_utils.format_exception(response_or_exception,
                                                  use_bracket=True))
                raise fastapi.HTTPException(
                    # 500 means internal server error.
                    status_code=500,
                    detail=f'Max retries {constants.LB_MAX_RETRY} exceeded. '
                    f'Last error encountered: {exception}. Please use '
                    '"sky serve logs [SERVICE_NAME] --load-balancer" '
                    'for more information.')
            current_backoff = backoff.current_backoff()
            logger.error(f'Retry in {current_backoff} seconds.')
            await asyncio.sleep(current_backoff)

    def run(self):
        self._app.add_api_route('/{path:path}',
                                self._proxy_with_retries,
                                methods=['GET', 'POST', 'PUT', 'DELETE'])

        @self._app.on_event('startup')
        async def startup():
            # Configure logger
            uvicorn_access_logger = logging.getLogger('uvicorn.access')
            for handler in uvicorn_access_logger.handlers:
                handler.setFormatter(sky_logging.FORMATTER)

            # Register controller synchronization task
            asyncio.create_task(self._sync_with_controller())

            # Register the stalled request monitoring task
            asyncio.create_task(self._monitor_stalled_requests())

        uvicorn_tls_kwargs = ({} if self._tls_credential is None else
                              self._tls_credential.dump_uvicorn_kwargs())

        protocol = 'https' if self._tls_credential is not None else 'http'

        logger.info('SkyServe Load Balancer started on '
                    f'{protocol}://0.0.0.0:{self._load_balancer_port}')
        logger.info(
            f'First byte timeout set to {FIRST_BYTE_TIMEOUT_SECONDS} seconds')
        logger.info(
            f'Request stealing interval set to {REQUEST_STEALING_INTERVAL_SECONDS} seconds'
        )
        logger.info(f'Maximum steal attempts per request: {MAX_STEAL_ATTEMPTS}')

        uvicorn.run(self._app,
                    host='0.0.0.0',
                    port=self._load_balancer_port,
                    **uvicorn_tls_kwargs)


def run_load_balancer(
        controller_addr: str,
        load_balancer_port: int,
        load_balancing_policy_name: Optional[str] = None,
        tls_credential: Optional[serve_utils.TLSCredential] = None) -> None:
    """ Run the load balancer.

    Args:
        controller_addr: The address of the controller.
        load_balancer_port: The port where the load balancer listens to.
        policy_name: The name of the load balancing policy to use. Defaults to
            None.
    """
    load_balancer = SkyServeLoadBalancer(
        controller_url=controller_addr,
        load_balancer_port=load_balancer_port,
        load_balancing_policy_name=load_balancing_policy_name,
        tls_credential=tls_credential)
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
        '--first-byte-timeout',
        type=float,
        default=FIRST_BYTE_TIMEOUT_SECONDS,
        help='Timeout in seconds for detecting stalled requests')
    parser.add_argument(
        '--request-stealing-interval',
        type=float,
        default=REQUEST_STEALING_INTERVAL_SECONDS,
        help='Interval in seconds for checking stealing opportunities')
    parser.add_argument(
        '--max-steal-attempts',
        type=int,
        default=MAX_STEAL_ATTEMPTS,
        help='Maximum number of times to attempt stealing a request')
    args = parser.parse_args()

    # Update the timeouts and limits if specified
    if args.first_byte_timeout:
        FIRST_BYTE_TIMEOUT_SECONDS = args.first_byte_timeout
    if args.request_stealing_interval:
        REQUEST_STEALING_INTERVAL_SECONDS = args.request_stealing_interval
    if args.max_steal_attempts:
        MAX_STEAL_ATTEMPTS = args.max_steal_attempts

    run_load_balancer(args.controller_addr, args.load_balancer_port,
                      args.load_balancing_policy)

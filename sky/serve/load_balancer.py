"""LoadBalancer: Distribute any incoming request to all ready replicas."""
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Union

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

_IS_FROM_LB_HEADER = 'X-Sky-Serve-From-LB'


class ClientPool:
    """ClientPool: A pool of httpx.AsyncClient for the load balancer.

    This class is used to manage the client pool for the load balancer.
    It also incorporates the load balancing policy to select the replica.
    """

    def __init__(self, load_balancing_policy_name: Optional[str]) -> None:
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
        # We need this lock to avoid getting from the client pool while
        # updating it from _sync_with_controller.
        self._lock: threading.Lock = threading.Lock()

    async def refresh_with_new_urls(
            self, ready_urls: List[str]) -> List[asyncio.Task]:
        close_client_tasks = []
        with self._lock:
            await self._load_balancing_policy.set_ready_replicas(ready_urls)
            for replica_url in ready_urls:
                if replica_url not in self._pool:
                    self._pool[replica_url] = httpx.AsyncClient(
                        base_url=replica_url)
            urls_to_close = set(self._pool.keys()) - set(ready_urls)
            for replica_url in urls_to_close:
                client = self._pool.pop(replica_url)
                close_client_tasks.append(client.aclose())
        return close_client_tasks

    def select_replica(self, request: fastapi.Request) -> Optional[str]:
        with self._lock:
            return self._load_balancing_policy.select_replica(request)

    def get_client(self, url: str) -> Optional[httpx.AsyncClient]:
        with self._lock:
            return self._pool.get(url, None)

    def pre_execute_hook(self, url: str, request: fastapi.Request) -> None:
        with self._lock:
            self._load_balancing_policy.pre_execute_hook(url, request)

    def post_execute_hook(self, url: str, request: fastapi.Request) -> None:
        with self._lock:
            self._load_balancing_policy.post_execute_hook(url, request)


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
        self._request_aggregator: serve_utils.RequestsAggregator = (
            serve_utils.RequestTimestamp())
        self._region: Optional[str] = region
        self._tls_credential: Optional[serve_utils.TLSCredential] = (
            tls_credential)
        self._replica_pool: ClientPool = ClientPool(load_balancing_policy_name)
        self._lb_pool: ClientPool = ClientPool(meta_load_balancing_policy_name)
        # TODO(tian): Temporary debugging solution. Remove this in production.
        self._replica2id: Dict[str, str] = {}
        self._lb2region: Dict[str, str] = {}

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
            self, url: str, request: fastapi.Request,
            is_from_lb: bool) -> Union[fastapi.responses.Response, Exception]:
        """Proxy the request to the specified URL.

        Returns:
            The response from the endpoint replica. Return the exception
            encountered if anything goes wrong.
        """
        if is_from_lb:
            log_key = 'replica-decision'
            replica_id = self._replica2id.get(url, 'N/A')
            log_to_request = (f'Select Replica with id {replica_id} ({url})')
        else:
            log_key = 'lb-decision'
            lb_region = self._lb2region.get(url, 'N/A')
            log_to_request = (f'Select LB in region {lb_region} ({url})')
        logger.info(f'Proxy request to {url}')
        pool_to_use = self._replica_pool if is_from_lb else self._lb_pool
        pool_to_use.pre_execute_hook(url, request)
        try:
            # We defer the get of the client here on purpose, for case when the
            # replica is ready in `_proxy_with_retries` but refreshed before
            # entering this function. In that case we will return an error here
            # and retry to find next ready replica. We also need to wait for the
            # update of the client pool to finish before getting the client.
            client = pool_to_use.get_client(url)
            if client is None:
                return RuntimeError(f'Client for {url} not found.')
            worker_url = httpx.URL(path=request.url.path,
                                   query=request.url.query.encode('utf-8'))
            headers = request.headers.mutablecopy()
            if not is_from_lb:
                # If it is not from LB, then the following request will be sent
                # out from LB. So we add the header to indicate it.
                headers[_IS_FROM_LB_HEADER] = 'true'
            proxy_request = client.build_request(
                request.method,
                worker_url,
                headers=headers.raw,
                content=await request.body(),
                timeout=constants.LB_STREAM_TIMEOUT)
            proxy_response = await client.send(proxy_request, stream=True)

            async def background_func():
                await proxy_response.aclose()
                pool_to_use.post_execute_hook(url, request)

            proxy_response.headers.update({log_key: log_to_request})
            return fastapi.responses.StreamingResponse(
                content=proxy_response.aiter_raw(),
                status_code=proxy_response.status_code,
                headers=proxy_response.headers,
                background=background.BackgroundTask(background_func))
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f'Error when proxy request to {url}: '
                         f'{common_utils.format_exception(e)}')
            return e

    async def _proxy_with_retries(
            self, request: fastapi.Request) -> fastapi.responses.Response:
        """Try to proxy the request to the endpoint replica with retries."""
        self._request_aggregator.add(request)
        # Check if request is from another load balancer to avoid infinite loops
        is_from_lb = request.headers.get(_IS_FROM_LB_HEADER, False)
        pool_to_use = self._replica_pool if is_from_lb else self._lb_pool
        source_identity = 'LB' if is_from_lb else 'User'
        logger.info(f'Received request {request.url} from {source_identity}.')
        # TODO(tian): Finetune backoff parameters.
        backoff = common_utils.Backoff(initial_backoff=1)
        # SkyServe supports serving on Spot Instances. To avoid preemptions
        # during request handling, we add a retry here.
        retry_cnt = 0
        while True:
            retry_cnt += 1
            # TODO(tian): If route to self, not adding another layer of proxy.
            ready_replica_url = pool_to_use.select_replica(request)
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
                    ready_replica_url, request, is_from_lb)
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

    async def _health_check(self) -> fastapi.responses.Response:
        """Health check endpoint."""
        return fastapi.responses.Response(status_code=200)

    def run(self):
        # Add health check endpoint first so it takes precedence
        self._app.add_api_route(constants.LB_HEALTH_ENDPOINT,
                                self._health_check,
                                methods=['GET'])

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
        meta_load_balancing_policy_name=meta_load_balancing_policy_name,
        region=region,
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
        '--meta-load-balancing-policy',
        choices=available_policies,
        default=lb_policies.DEFAULT_LB_POLICY,
        help=f'The meta load balancing policy to use. Available policies: '
        f'{", ".join(available_policies)}.')
    parser.add_argument('--region',
                        required=True,
                        help='The region of the load balancer.')
    args = parser.parse_args()
    run_load_balancer(args.controller_addr, args.load_balancer_port,
                      args.load_balancing_policy,
                      args.meta_load_balancing_policy, args.region)

"""LoadBalancer: Distribute any incoming request to all ready replicas."""
import asyncio
import logging
import threading
from typing import Dict, Optional, Union

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


class SkyServeLoadBalancer:
    """SkyServeLoadBalancer: distribute incoming traffic with proxy.

    This class accept any traffic to the controller and proxies it
    to the appropriate endpoint replica according to the load balancing
    policy.
    """

    def __init__(self,
                 controller_url: str,
                 load_balancer_port: int,
                 load_balancing_policy_name: Optional[str] = None) -> None:
        """Initialize the load balancer.

        Args:
            controller_url: The URL of the controller.
            load_balancer_port: The port where the load balancer listens to.
            load_balancing_policy_name: The name of the load balancing policy
                to use. Defaults to None.
        """
        self._app = fastapi.FastAPI()
        self._controller_url: str = controller_url
        self._load_balancer_port: int = load_balancer_port
        # Use the registry to create the load balancing policy
        self._load_balancing_policy = lb_policies.LoadBalancingPolicy.make(
            load_balancing_policy_name)
        self._request_aggregator: serve_utils.RequestsAggregator = (
            serve_utils.RequestTimestamp())
        # TODO(tian): httpx.Client has a resource limit of 100 max connections
        # for each client. We should wait for feedback on the best max
        # connections.
        # Reference: https://www.python-httpx.org/advanced/resource-limits/
        #
        # If more than 100 requests are sent to the same replica, the
        # httpx.Client will queue the requests and send them when a
        # connection is available.
        # Reference: https://github.com/encode/httpcore/blob/a8f80980daaca98d556baea1783c5568775daadc/httpcore/_async/connection_pool.py#L69-L71 # pylint: disable=line-too-long
        self._client_pool: Dict[str, httpx.AsyncClient] = dict()
        # We need this lock to avoid getting from the client pool while
        # updating it from _sync_with_controller.
        self._client_pool_lock: threading.Lock = threading.Lock()

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
                        for replica_url in ready_replica_urls:
                            if replica_url not in self._client_pool:
                                self._client_pool[replica_url] = (
                                    httpx.AsyncClient(base_url=replica_url))
                        urls_to_close = set(
                            self._client_pool.keys()) - set(ready_replica_urls)
                        client_to_close = []
                        for replica_url in urls_to_close:
                            client_to_close.append(
                                self._client_pool.pop(replica_url))
                    for client in client_to_close:
                        close_client_tasks.append(client.aclose())

            await asyncio.sleep(constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS)
            # Await those tasks after the interval to avoid blocking.
            await asyncio.gather(*close_client_tasks)

    async def _proxy_request_to(
        self, url: str, request: fastapi.Request
    ) -> Union[fastapi.responses.Response, Exception]:
        """Proxy the request to the specified URL.

        Returns:
            The response from the endpoint replica. Return the exception
            encountered if anything goes wrong.
        """
        logger.info(f'Proxy request to {url}')
        try:
            # We defer the get of the client here on purpose, for case when the
            # replica is ready in `_proxy_with_retries` but refreshed before
            # entering this function. In that case we will return an error here
            # and retry to find next ready replica. We also need to wait for the
            # update of the client pool to finish before getting the client.
            with self._client_pool_lock:
                client = self._client_pool.get(url, None)
            if client is None:
                return RuntimeError(f'Client for {url} not found.')
            worker_url = httpx.URL(path=request.url.path,
                                   query=request.url.query.encode('utf-8'))
            proxy_request = client.build_request(
                request.method,
                worker_url,
                headers=request.headers.raw,
                content=await request.body(),
                timeout=constants.LB_STREAM_TIMEOUT)
            proxy_response = await client.send(proxy_request, stream=True)
            return fastapi.responses.StreamingResponse(
                content=proxy_response.aiter_raw(),
                status_code=proxy_response.status_code,
                headers=proxy_response.headers,
                background=background.BackgroundTask(proxy_response.aclose))
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f'Error when proxy request to {url}: '
                         f'{common_utils.format_exception(e)}')
            return e

    async def _proxy_with_retries(
            self, request: fastapi.Request) -> fastapi.responses.Response:
        """Try to proxy the request to the endpoint replica with retries."""
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
                    ready_replica_url, request)
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

        logger.info('SkyServe Load Balancer started on '
                    f'http://0.0.0.0:{self._load_balancer_port}')

        uvicorn.run(self._app, host='0.0.0.0', port=self._load_balancer_port)


def run_load_balancer(controller_addr: str,
                      load_balancer_port: int,
                      load_balancing_policy_name: Optional[str] = None) -> None:
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
        load_balancing_policy_name=load_balancing_policy_name)
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
        default='round_robin',
        help=f'The load balancing policy to use. Available policies: '
        f'{", ".join(available_policies)}.')
    args = parser.parse_args()
    run_load_balancer(args.controller_addr, args.load_balancer_port,
                      args.load_balancing_policy)

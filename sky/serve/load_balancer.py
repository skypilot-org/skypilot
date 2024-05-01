"""LoadBalancer: redirect any incoming request to an endpoint replica."""
import asyncio
import logging
import threading
import time
from typing import Optional

import fastapi
import httpx
import requests
import uvicorn

from sky import sky_logging
from sky.serve import constants
from sky.serve import load_balancing_policies as lb_policies
from sky.serve import serve_utils
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)


class SkyServeLoadBalancer:
    """SkyServeLoadBalancer: proxy incoming traffic.

    This class accept any traffic to the controller and proxies it
    to the appropriate endpoint replica according to the load balancing
    policy.
    """

    def __init__(self, controller_url: str, load_balancer_port: int) -> None:
        """Initialize the load balancer.

        Args:
            controller_url: The URL of the controller.
            load_balancer_port: The port where the load balancer listens to.
        """
        self._app = fastapi.FastAPI()
        self._controller_url = controller_url
        self._load_balancer_port = load_balancer_port
        self._load_balancing_policy: lb_policies.LoadBalancingPolicy = (
            lb_policies.RoundRobinPolicy())
        self._request_aggregator: serve_utils.RequestsAggregator = (
            serve_utils.RequestTimestamp())

    def _sync_with_controller(self):
        """Sync with controller periodically.

        Every `constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS` seconds, the
        load balancer will sync with the controller to get the latest
        information about available replicas; also, it report the request
        information to the controller, so that the controller can make
        autoscaling decisions.
        """
        # Sleep for a while to wait the controller bootstrap.
        time.sleep(5)

        while True:
            with requests.Session() as session:
                try:
                    # Send request information
                    response = session.post(
                        self._controller_url + '/controller/load_balancer_sync',
                        json={
                            'request_aggregator':
                                self._request_aggregator.to_dict()
                        },
                        timeout=5)
                    # Clean up after reporting request information to avoid OOM.
                    self._request_aggregator.clear()
                    response.raise_for_status()
                    ready_replica_urls = response.json().get(
                        'ready_replica_urls')
                except requests.RequestException as e:
                    print(f'An error occurred: {e}')
                else:
                    logger.info(f'Available Replica URLs: {ready_replica_urls}')
                    self._load_balancing_policy.set_ready_replicas(
                        ready_replica_urls)
            time.sleep(constants.LB_CONTROLLER_SYNC_INTERVAL_SECONDS)

    async def _proxy_request_to(
            self, url: str,
            request: fastapi.Request) -> Optional[fastapi.responses.Response]:
        """Proxy the request to the specified URL.

        Returns:
            The response from the endpoint replica. None if anything goes wrong.
        """
        method = request.method
        headers = {key: value for key, value in request.headers.items()}
        body = await request.body()
        path = f'http://{url}{request.url.path}'
        logger.info(f'Proxy request to {path}')
        try:

            async def stream_response():
                async with httpx.AsyncClient() as client:
                    async with client.stream(method,
                                             path,
                                             headers=headers,
                                             content=body) as response:
                        response.raise_for_status()
                        # TODO(tian): Hacky. Investigate a way to not directly
                        # yielding the response status code and headers.
                        yield response.status_code
                        yield dict(response.headers)
                        try:
                            async for chunk in response.aiter_bytes():
                                yield chunk
                        finally:
                            await response.aclose()

            content = stream_response()
            status_code = await content.__anext__()
            headers = await content.__anext__()
            return fastapi.responses.StreamingResponse(content=content,
                                                       status_code=status_code,
                                                       headers=headers)
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f'Error when proxy request to {path}: '
                         f'{common_utils.format_exception(e)}')
            return None

    async def _proxy_with_retries(
            self, request: fastapi.Request) -> fastapi.responses.Response:
        """Try to proxy the request to the endpoint replica with retries."""
        self._request_aggregator.add(request)
        # TODO(tian): Finetune backoff parameters.
        backoff = common_utils.Backoff(initial_backoff=1)
        # SkyServe supports serving on Spot Instances. To avoid preemptions
        # during request handling, we add a retry here.
        # TODO(tian): Max number of retries.
        while True:
            ready_replica_url = self._load_balancing_policy.select_replica(
                request)
            if ready_replica_url is None:
                raise fastapi.HTTPException(
                    status_code=503,
                    detail='No ready replicas. '
                    'Use "sky serve status [SERVICE_NAME]" '
                    'to check the replica status.')
            response = await self._proxy_request_to(ready_replica_url, request)
            if response is not None:
                return response
            current_backoff = backoff.current_backoff()
            logger.error(f'Retry in {current_backoff} seconds.')
            await asyncio.sleep(current_backoff)

    def run(self):
        self._app.add_api_route('/{path:path}',
                                self._proxy_with_retries,
                                methods=['GET', 'POST', 'PUT', 'DELETE'])

        @self._app.on_event('startup')
        def configure_logger():
            uvicorn_access_logger = logging.getLogger('uvicorn.access')
            for handler in uvicorn_access_logger.handlers:
                handler.setFormatter(sky_logging.FORMATTER)

        threading.Thread(target=self._sync_with_controller, daemon=True).start()

        logger.info('SkyServe Load Balancer started on '
                    f'http://0.0.0.0:{self._load_balancer_port}')

        uvicorn.run(self._app, host='0.0.0.0', port=self._load_balancer_port)


def run_load_balancer(controller_addr: str, load_balancer_port: int):
    load_balancer = SkyServeLoadBalancer(controller_url=controller_addr,
                                         load_balancer_port=load_balancer_port)
    load_balancer.run()

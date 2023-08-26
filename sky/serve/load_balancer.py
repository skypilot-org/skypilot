"""LoadBalancer: redirect any incoming request to an endpoint replica."""
import argparse
import threading
import time

import fastapi
import requests
import uvicorn

from sky import sky_logging
from sky.serve import constants
from sky.serve import load_balancing_algorithms

# Use the explicit logger name so that the logger is under the
# `sky.serve.load_balancer` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.load_balancer')

_LBAlgorithm = load_balancing_algorithms.LoadBalancingAlgorithm


class SkyServeLoadBalancer:
    """SkyServeLoadBalancer: redirect incoming traffic.

    This class accept any traffic to the controller and redirect it
    to the appropriate endpoint replica according to the load balancing
    algorithm.
    """

    def __init__(self, controller_url: str, port: int,
                 load_balancing_algorithm: _LBAlgorithm) -> None:
        self.app = fastapi.FastAPI()
        self.controller_url = controller_url
        self.port = port
        self.lb_algorithm = load_balancing_algorithm

        for i in range(3):
            resp = requests.get(self.controller_url +
                                '/controller/get_autoscaler_query_interval')
            if resp.status_code == 200:
                self.lb_algorithm.set_query_interval(
                    resp.json()['query_interval'])
                break
            if i == 2:
                logger.error('Failed to get autoscaler query interval. '
                             'Use default interval instead.')
                self.lb_algorithm.set_query_interval(None)
            time.sleep(10)

    def _sync_with_controller(self):
        while True:
            with requests.Session() as session:
                try:
                    # send request num in last query interval
                    response = session.post(
                        self.controller_url + '/controller/update_num_requests',
                        json={
                            'num_requests':
                                self.lb_algorithm.deprecate_old_requests()
                        },
                        timeout=5)
                    response.raise_for_status()
                    # get replica ips
                    response = session.get(self.controller_url +
                                           '/controller/get_ready_replicas')
                    response.raise_for_status()
                    ready_replicas = response.json()['ready_replicas']
                except requests.RequestException as e:
                    print(f'An error occurred: {e}')
                else:
                    logger.info(f'Available Replica IPs: {ready_replicas}')
                    self.lb_algorithm.set_ready_replicas(ready_replicas)
            time.sleep(constants.CONTROLLER_SYNC_INTERVAL)

    async def _redirect_handler(self, request: fastapi.Request):
        self.lb_algorithm.increment_request_count(1)
        replica_ip = self.lb_algorithm.select_replica(request)

        if replica_ip is None:
            raise fastapi.HTTPException(status_code=503,
                                        detail='No available replicas. '
                                        'Use "sky serve status [SERVICE_ID]" '
                                        'to check the replica status.')

        path = f'http://{replica_ip}:{self.port}{request.url.path}'
        logger.info(f'Redirecting request to {path}')
        return fastapi.responses.RedirectResponse(url=path)

    def run(self):
        self.app.add_api_route('/{path:path}',
                               self._redirect_handler,
                               methods=['GET', 'POST', 'PUT', 'DELETE'])

        sync_controller_thread = threading.Thread(
            target=self._sync_with_controller, daemon=True)
        sync_controller_thread.start()

        logger.info(
            f'SkyServe Load Balancer started on http://0.0.0.0:{self.port}')

        uvicorn.run(self.app, host='0.0.0.0', port=self.port)


if __name__ == '__main__':
    # Add argparse
    parser = argparse.ArgumentParser(description='SkyServe Load Balancer')
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--port',
                        '-p',
                        type=int,
                        help='Port to run the load balancer on.',
                        required=True)
    parser.add_argument('--controller-addr',
                        type=str,
                        help='Controller address (ip:port).',
                        required=True)
    args = parser.parse_args()

    # ======= Load Balancing Algorithm =========
    _load_balancing_algorithm = (
        load_balancing_algorithms.RoundRobinLoadBalancingAlgorithm())

    # ======= SkyServeLoadBalancer =========
    load_balancer = SkyServeLoadBalancer(
        controller_url=args.controller_addr,
        port=args.port,
        load_balancing_algorithm=_load_balancing_algorithm)
    load_balancer.run()

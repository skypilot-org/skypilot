"""LoadBalancer: redirect any incoming request to an endpoint replica."""
import argparse
import base64
import pickle
import threading
import time

import fastapi
import requests
import uvicorn

from sky import sky_logging
from sky.serve import constants
from sky.serve import load_balancing_policies
from sky.serve import serve_utils

# Use the explicit logger name so that the logger is under the
# `sky.serve.load_balancer` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.load_balancer')


class SkyServeLoadBalancer:
    """SkyServeLoadBalancer: redirect incoming traffic.

    This class accept any traffic to the controller and redirect it
    to the appropriate endpoint replica according to the load balancing
    policy.
    """

    def __init__(
        self, controller_url: str, load_balancer_port: int, replica_port: int,
        load_balancing_policy: load_balancing_policies.LoadBalancingPolicy
    ) -> None:
        self.app = fastapi.FastAPI()
        self.controller_url = controller_url
        # This is the port where the load balancer listens to.
        self.load_balancer_port = load_balancer_port
        # This is the port where the replica app listens to.
        self.replica_port = replica_port
        self.load_balancing_policy = load_balancing_policy
        self.request_information: serve_utils.RequestInformation = (
            serve_utils.RequestTimestamp())

    def _sync_with_controller(self):
        while True:
            with requests.Session() as session:
                try:
                    # TODO(tian): Maybe merge all of them into one request?
                    # check if the controller is terminating. If so, shut down
                    # the load balancer so the skypilot jobs will finish, thus
                    # enable the controller VM to autostop.
                    response = session.get(self.controller_url +
                                           '/controller/is_terminating')
                    response.raise_for_status()
                    logger.debug(
                        f'Controller terminating status: {response.json()}')
                    if response.json().get('is_terminating'):
                        logger.info('Controller is terminating. '
                                    'Shutting down load balancer.')
                        serve_utils.kill_children_and_self_processes()
                    # send request information
                    response = session.post(
                        self.controller_url +
                        '/controller/report_request_information',
                        json={
                            'request_information': base64.b64encode(
                                pickle.dumps(self.request_information)
                            ).decode('utf-8')
                        },
                        timeout=5)
                    # Clean up after reporting request information to avoid OOM.
                    self.request_information.clear()
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
                    self.load_balancing_policy.set_ready_replicas(
                        ready_replicas)
            time.sleep(constants.CONTROLLER_SYNC_INTERVAL)

    async def _redirect_handler(self, request: fastapi.Request):
        self.request_information.add(request)
        replica_ip = self.load_balancing_policy.select_replica(request)

        if replica_ip is None:
            raise fastapi.HTTPException(status_code=503,
                                        detail='No available replicas. '
                                        'Use "sky serve status [SERVICE_ID]" '
                                        'to check the replica status.')

        path = f'http://{replica_ip}:{self.replica_port}{request.url.path}'
        logger.info(f'Redirecting request to {path}')
        return fastapi.responses.RedirectResponse(url=path)

    def run(self):
        self.app.add_api_route('/{path:path}',
                               self._redirect_handler,
                               methods=['GET', 'POST', 'PUT', 'DELETE'])

        sync_controller_thread = threading.Thread(
            target=self._sync_with_controller, daemon=True)
        sync_controller_thread.start()

        logger.info('SkyServe Load Balancer started on '
                    f'http://0.0.0.0:{self.load_balancer_port}')

        uvicorn.run(self.app, host='0.0.0.0', port=self.load_balancer_port)


if __name__ == '__main__':
    # Add argparse
    parser = argparse.ArgumentParser(description='SkyServe Load Balancer')
    parser.add_argument('--load-balancer-port',
                        type=int,
                        help='Port to run the load balancer on.',
                        required=True)
    parser.add_argument('--replica-port',
                        type=int,
                        help='Port that runs app on replica.',
                        required=True)
    parser.add_argument('--controller-addr',
                        type=str,
                        help='Controller address (ip:port).',
                        required=True)
    args = parser.parse_args()

    # ======= Load Balancing Policy =========
    _load_balancing_policy = load_balancing_policies.RoundRobinPolicy()

    # ======= SkyServeLoadBalancer =========
    load_balancer = SkyServeLoadBalancer(
        controller_url=args.controller_addr,
        load_balancer_port=args.load_balancer_port,
        replica_port=args.replica_port,
        load_balancing_policy=_load_balancing_policy)
    load_balancer.run()

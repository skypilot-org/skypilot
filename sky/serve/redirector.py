"""Redirector: redirect any incoming request to an endpoint replica."""
import time
import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
import threading
import uvicorn

import requests
import argparse

from sky.serve.load_balancers import RoundRobinLoadBalancer, LoadBalancer

CONTROL_PLANE_SYNC_INTERVAL = 20

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-6s | %(name)-40s || %(message)s',
    datefmt='%m-%d %H:%M:%S',
    # force=True,
)
logger = logging.getLogger(__name__)


class SkyServeRedirector:
    """Redirector: redirect incoming traffic.

    This class accept any traffic to the controller and redirect it
    to the appropriate endpoint replica.
    """

    def __init__(self, control_plane_url: str, port: int,
                 load_balancer: LoadBalancer):
        self.app = FastAPI()
        self.control_plane_url = control_plane_url
        self.port = port
        self.load_balancer = load_balancer

        for i in range(3):
            resp = requests.get(self.control_plane_url +
                                '/control_plane/get_autoscaler_query_interval')
            if resp.status_code == 200:
                self.load_balancer.set_query_interval(
                    resp.json()['query_interval'])
                break
            if i == 2:
                logger.error('Failed to get autoscaler query interval. '
                             'Use default interval instead.')
                self.load_balancer.set_query_interval(None)
            time.sleep(10)

    def sync_with_control_plane(self):
        while True:
            with requests.Session() as session:
                try:
                    # send request num in last query interval
                    response = session.post(
                        self.control_plane_url +
                        '/control_plane/get_num_requests',
                        json={
                            'num_requests':
                                self.load_balancer.deprecate_old_requests()
                        },
                        timeout=5)
                    response.raise_for_status()
                    # get server ips
                    response = session.get(
                        self.control_plane_url +
                        '/control_plane/get_available_servers')
                    response.raise_for_status()
                    available_servers = response.json()['available_servers']
                except requests.RequestException as e:
                    print(f'An error occurred: {e}')
                else:
                    logger.info(f'Available Server IPs: {available_servers}')
                    self.load_balancer.set_available_servers(available_servers)
            time.sleep(CONTROL_PLANE_SYNC_INTERVAL)

    async def redirector_handler(self, request: Request):
        self.load_balancer.increment_request_count(1)
        server_ip = self.load_balancer.select_server(request)

        if server_ip is None:
            raise HTTPException(status_code=503, detail='No available servers')

        path = f'http://{server_ip}:{self.port}{request.url.path}'
        logger.info(f'Redirecting request to {path}')
        return RedirectResponse(url=path)

    def serve(self):
        self.app.add_api_route('/{path:path}',
                               self.redirector_handler,
                               methods=['GET', 'POST', 'PUT', 'DELETE'])

        server_fetcher_thread = threading.Thread(
            target=self.sync_with_control_plane, daemon=True)
        server_fetcher_thread.start()

        logger.info(f'Sky Server started on http://0.0.0.0:{self.port}')
        logger.info('Sky Serve Redirector is ready to serve.')

        uvicorn.run(self.app, host='0.0.0.0', port=self.port)


if __name__ == '__main__':
    # Add argparse
    parser = argparse.ArgumentParser(description='SkyServe Redirector')
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--port',
                        '-p',
                        type=int,
                        help='Port to run the redirector on.',
                        required=True)
    parser.add_argument('--control-plane-addr',
                        type=str,
                        help='Control plane address (ip:port).',
                        required=True)
    args = parser.parse_args()

    # ======= Load Balancer =========
    _load_balancer = RoundRobinLoadBalancer()

    # ======= Redirector =========
    redirector = SkyServeRedirector(control_plane_url=args.control_plane_addr,
                                    port=args.port,
                                    load_balancer=_load_balancer)
    redirector.serve()

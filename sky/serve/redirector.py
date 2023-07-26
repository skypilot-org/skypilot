"""Redirector: redirect any incoming request to an endpoint replica."""
import time
import logging
from collections import deque
from typing import List, Deque

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
import threading
import uvicorn

import requests
import argparse

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

    def __init__(self, control_plane_url: str, port: int):
        self.control_plane_url = control_plane_url
        self.port = port
        self.server_ips: List[str] = []
        self.servers_queue: Deque[str] = deque()
        self.app = FastAPI()
        self.request_count = 0
        self.control_plane_sync_timeout = 20

    def sync_with_control_plane(self):
        while True:
            server_ips = []
            with requests.Session() as session:
                try:
                    # send request count
                    response = session.post(
                        self.control_plane_url +
                        '/control_plane/increment_request_count',
                        json={'counts': self.request_count},
                        timeout=5)
                    response.raise_for_status()
                    self.request_count = 0
                    # get server ips
                    response = session.get(self.control_plane_url +
                                           '/control_plane/get_server_ips')
                    response.raise_for_status()
                    server_ips = response.json()['server_ips']
                except requests.RequestException as e:
                    print(f'An error occurred: {e}')
                else:
                    logger.info(f'Server IPs: {server_ips}')
                    self.servers_queue = deque(server_ips)
            time.sleep(self.control_plane_sync_timeout)

    def select_server(self):
        if not self.servers_queue:
            return None
        server_ip = self.servers_queue.popleft()
        self.servers_queue.append(server_ip)
        return server_ip

    async def redirector_handler(self, request: Request):
        self.request_count += 1
        server_ip = self.select_server()

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

    redirector = SkyServeRedirector(control_plane_url=args.control_plane_addr,
                                    port=args.port)
    redirector.serve()

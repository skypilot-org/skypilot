import time
import logging
import yaml
from collections import deque
from typing import List, Deque

from sky.serve.common import SkyServiceSpec

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

    def __init__(self,
                 controller_url: str,
                 service_spec: SkyServiceSpec,
                 port: int = 8081):
        self.controller_url = controller_url
        self.port = port
        self.app_port = service_spec.app_port
        self.server_ips: List[str] = []
        self.servers_queue: Deque[str] = deque()
        self.app = FastAPI()
        self.request_count = 0
        self.controller_sync_timeout = 20

    def sync_with_controller(self):
        while True:
            server_ips = []
            with requests.Session() as session:
                try:
                    # send request count
                    response = session.post(
                        self.controller_url +
                        '/controller/increment_request_count',
                        json={'counts': self.request_count},
                        timeout=5)
                    response.raise_for_status()
                    self.request_count = 0
                    # get server ips
                    response = session.get(self.controller_url +
                                           '/controller/get_server_ips')
                    response.raise_for_status()
                    server_ips = response.json()['server_ips']
                except requests.RequestException as e:
                    print(f'An error occurred: {e}')
                else:
                    logger.info(f'Server IPs: {server_ips}')
                    self.servers_queue = deque(server_ips)
            time.sleep(self.controller_sync_timeout)

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
        logger.info(f'Redirecting request to {server_ip}{request.url.path}')

        path = f'http://{server_ip}:{self.app_port}{request.url.path}'
        logger.info(f'Redirecting request to {path}')
        return RedirectResponse(url=path)

    def serve(self):
        self.app.add_api_route('/{path:path}',
                               self.redirector_handler,
                               methods=['GET', 'POST', 'PUT', 'DELETE'])

        server_fetcher_thread = threading.Thread(
            target=self.sync_with_controller, daemon=True)
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
                        help='Port to run the redirector on',
                        required=True)
    parser.add_argument('--controller-addr',
                        default='http://localhost:8082',
                        type=str,
                        help='Controller address (ip:port).',
                        required=True)
    args = parser.parse_args()

    with open(args.task_yaml, 'r') as f:
        task = yaml.safe_load(f)
    if 'service' not in task:
        raise ValueError('Task YAML must have a "service" section')
    service_config = task['service']
    service_spec = SkyServiceSpec.from_yaml_config(service_config)

    redirector = SkyServeRedirector(controller_url=args.controller_addr,
                                    service_spec=service_spec,
                                    port=args.port)
    redirector.serve()

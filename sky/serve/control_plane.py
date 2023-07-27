"""Control Plane: the central control plane of SkyServe.

Responsible for autoscaling and server management.
"""
import logging

import argparse

from sky.serve.autoscalers import RequestRateAutoscaler, Autoscaler
from sky.serve import SkyServiceSpec
from sky.serve.infra_providers import InfraProvider, SkyPilotInfraProvider
from sky.serve.load_balancers import RoundRobinLoadBalancer, LoadBalancer

import time
import threading

from typing import Optional

from fastapi import FastAPI, Request
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-6s | %(name)-40s || %(message)s',
    datefmt='%m-%d %H:%M:%S',
    force=True)
logger = logging.getLogger(__name__)


class ControlPlane:
    """Control Plane: control everything about server.

    This class is responsible for:
        - Starting and terminating the server monitor and autoscaler.
        - Providing the HTTP Server API for SkyServe to communicate with.
    """

    def __init__(self,
                 port: int,
                 infra_provider: InfraProvider,
                 load_balancer: LoadBalancer,
                 autoscaler: Optional[Autoscaler] = None) -> None:
        self.port = port
        self.infra_provider = infra_provider
        self.load_balancer = load_balancer
        self.autoscaler = autoscaler
        self.app = FastAPI()

    def server_fetcher(self) -> None:
        while not self.server_fetcher_stop_event.is_set():
            logger.info('Running server fetcher.')
            try:
                server_ips = self.infra_provider.get_server_ips()
                self.infra_provider.probe_endpoints(server_ips)
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # server fetcher running.
                logger.error(f'Error in server fetcher: {e}')
            time.sleep(10)

    def start_server_fetcher(self) -> None:
        self.server_fetcher_stop_event = threading.Event()
        self.server_fetcher_thread = threading.Thread(
            target=self.server_fetcher)
        self.server_fetcher_thread.start()

    def terminate_server_fetcher(self) -> None:
        self.server_fetcher_stop_event.set()
        self.server_fetcher_thread.join()

    # TODO(tian): Authentication!!!
    def run(self) -> None:

        @self.app.post('/control_plane/increment_request_count')
        async def increment_request_count(request: Request):
            # await request
            request_data = await request.json()
            # get request data
            count = (0 if 'counts' not in request_data else
                     request_data['counts'])
            logger.info(f'Received request: {request_data}')
            self.load_balancer.increment_request_count(count=count)
            return {'message': 'Success'}

        @self.app.get('/control_plane/get_server_ips')
        def get_server_ips():
            return {'server_ips': list(self.infra_provider.available_servers)}

        @self.app.get('/control_plane/get_replica_info')
        def get_replica_info():
            return {'replica_info': self.infra_provider.get_replica_info()}

        @self.app.get('/control_plane/get_replica_nums')
        def get_replica_nums():
            return {
                'num_healthy_replicas': len(
                    self.infra_provider.available_servers),
                'num_unhealthy_replicas':
                    self.infra_provider.total_servers() -
                    len(self.infra_provider.available_servers),
                'num_failed_replicas': len(
                    self.infra_provider.get_failed_servers())
            }

        @self.app.post('/control_plane/terminate')
        def terminate(request: Request):
            del request
            # request_data = request.json()
            # TODO(tian): Authentication!!!
            logger.info('Terminating service...')
            self.terminate_server_fetcher()
            if self.autoscaler is not None:
                self.autoscaler.terminate_monitor()
            # For correctly show serve status
            self.infra_provider.available_servers.clear()
            self.infra_provider.terminate()
            return {'message': 'Success'}

        # Run server_monitor and autoscaler.monitor (if autoscaler is defined)
        # in separate threads in the background.
        # This should not block the main thread.
        self.start_server_fetcher()
        if self.autoscaler is not None:
            self.autoscaler.start_monitor()

        logger.info(f'Sky Server started on http://0.0.0.0:{self.port}')
        uvicorn.run(self.app, host='0.0.0.0', port=self.port)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe Control Plane')
    parser.add_argument('--service-name',
                        type=str,
                        help='Name of the service',
                        required=True)
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--port',
                        '-p',
                        type=int,
                        help='Port to run the control plane',
                        required=True)
    args = parser.parse_args()

    # ======= Infra Provider =========
    service_spec = SkyServiceSpec.from_yaml(args.task_yaml)
    _infra_provider = SkyPilotInfraProvider(
        args.task_yaml,
        args.service_name,
        readiness_path=service_spec.readiness_path,
        readiness_timeout=service_spec.readiness_timeout)

    # ======= Load Balancer =========
    _load_balancer = RoundRobinLoadBalancer()

    # ======= Autoscaler =========
    _autoscaler = RequestRateAutoscaler(
        _infra_provider,
        _load_balancer,
        frequency=5,
        min_nodes=service_spec.min_replica,
        max_nodes=service_spec.max_replica,
        upper_threshold=service_spec.qps_upper_threshold,
        lower_threshold=service_spec.qps_lower_threshold,
        cooldown=60)

    # ======= ControlPlane =========
    control_plane = ControlPlane(args.port, _infra_provider, _load_balancer,
                                 _autoscaler)
    control_plane.run()

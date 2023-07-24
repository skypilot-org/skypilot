import logging

import argparse

from sky.serve.autoscalers import RequestRateAutoscaler, Autoscaler
from sky.serve.common import SkyServiceSpec
from sky.serve.infra_providers import InfraProvider, SkyPilotInfraProvider
from sky.serve.load_balancers import RoundRobinLoadBalancer, LoadBalancer

import time
import threading
import yaml

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

    def __init__(self,
                 port: int,
                 infra_provider: InfraProvider,
                 load_balancer: LoadBalancer,
                 autoscaler: Optional[Autoscaler] = None):
        self.port = port
        self.infra_provider = infra_provider
        self.load_balancer = load_balancer
        self.autoscaler = autoscaler
        self.app = FastAPI()

    def server_fetcher(self):
        while True:
            logger.info('Running server fetcher.')
            server_ips = self.infra_provider.get_server_ips()
            self.load_balancer.probe_endpoints(server_ips)
            time.sleep(10)

    # TODO(tian): Authentication!!!
    def run(self):

        @self.app.post('/control_plane/increment_request_count')
        async def increment_request_count(request: Request):
            # await request
            request_data = await request.json()
            # get request data
            count = 0 if 'counts' not in request_data else request_data['counts']
            logger.info(f'Received request: {request_data}')
            self.load_balancer.increment_request_count(count=count)
            return {'message': 'Success'}

        @self.app.get('/control_plane/get_server_ips')
        def get_server_ips():
            return {'server_ips': list(self.load_balancer.servers_queue)}

        @self.app.get('/control_plane/get_replica_info')
        def get_replica_info():
            return {'replica_info': self.infra_provider.get_replica_info()}

        @self.app.get('/control_plane/get_replica_nums')
        def get_replica_nums():
            return {
                'num_healthy_replicas': len(self.load_balancer.available_servers
                                           ),
                'num_unhealthy_replicas':
                    self.infra_provider.total_servers() -
                    len(self.load_balancer.available_servers),
                # TODO(tian): Detect error replicas
                'num_failed_replicas': 0
            }

        # Run server_monitor and autoscaler.monitor (if autoscaler is defined) in separate threads in the background. This should not block the main thread.
        server_fetcher_thread = threading.Thread(target=self.server_fetcher,
                                                 daemon=True)
        server_fetcher_thread.start()
        if self.autoscaler:
            autoscaler_monitor_thread = threading.Thread(
                target=self.autoscaler.monitor, daemon=True)
            autoscaler_monitor_thread.start()

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
    # infra_provider = DummyInfraProvider()
    infra_provider = SkyPilotInfraProvider(args.task_yaml, args.service_name)

    # ======= Load Balancer =========
    with open(args.task_yaml, 'r') as f:
        task = yaml.safe_load(f)
    if 'service' not in task:
        raise ValueError('Task YAML must have a "service" section')
    service_config = task['service']
    service_spec = SkyServiceSpec.from_yaml_config(service_config)
    # Select the load balancing policy: RoundRobinLoadBalancer or LeastLoadedLoadBalancer
    load_balancer = RoundRobinLoadBalancer(
        infra_provider=infra_provider,
        endpoint_path=service_spec.readiness_path,
        readiness_timeout=service_spec.readiness_timeout)
    # load_balancer = LeastLoadedLoadBalancer(n=5)
    # autoscaler = LatencyThresholdAutoscaler(load_balancer,
    #                                         upper_threshold=0.5,    # 500ms
    #                                         lower_threshold=0.1)    # 100ms

    # ======= Autoscaler =========
    # Create an autoscaler with the RequestRateAutoscaler policy. Thresholds are defined as requests per node in the defined interval.
    autoscaler = RequestRateAutoscaler(
        infra_provider,
        load_balancer,
        frequency=5,
        min_nodes=service_spec.min_replica,
        max_nodes=service_spec.max_replica,
        upper_threshold=service_spec.qps_upper_threshold,
        lower_threshold=service_spec.qps_lower_threshold,
        cooldown=60)

    # ======= ControlPlane =========
    # Create a control plane object and run it.
    control_plane = ControlPlane(args.port, infra_provider, load_balancer,
                                 autoscaler)
    control_plane.run()

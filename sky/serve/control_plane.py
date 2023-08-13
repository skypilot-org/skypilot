"""Control Plane: the central control plane of SkyServe.

Responsible for autoscaling and replica management.
"""
import argparse
import base64
import fastapi
import logging
import pickle
from typing import Optional
import uvicorn

import sky
from sky import backends
from sky import serve
from sky.serve import autoscalers
from sky.serve import infra_providers
from sky.utils import env_options

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-6s | %(name)-40s || %(message)s',
    datefmt='%m-%d %H:%M:%S',
    force=True)
logger = logging.getLogger(__name__)


class SuppressSuccessGetAccessLogsFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not ('GET' in message and '200' in message)


class ControlPlane:
    """Control Plane: control everything about replica.

    This class is responsible for:
        - Starting and terminating the replica monitor and autoscaler.
        - Providing the HTTP Server API for SkyServe to communicate with.
    """

    def __init__(self,
                 port: int,
                 task_yaml: str,
                 infra_provider: infra_providers.InfraProvider,
                 autoscaler: Optional[autoscalers.Autoscaler] = None) -> None:
        self.port = port
        self.task_yaml = task_yaml
        self.infra_provider = infra_provider
        self.autoscaler = autoscaler
        self.app = fastapi.FastAPI()

    def run(self) -> None:

        @self.app.post('/control_plane/update_num_requests')
        async def update_num_requests(request: fastapi.Request):
            # await request
            request_data = await request.json()
            # get request data
            num_requests = request_data['num_requests']
            logger.info(f'Received request: {request_data}')
            if isinstance(self.autoscaler, autoscalers.RequestRateAutoscaler):
                self.autoscaler.set_num_requests(num_requests)
            return {'message': 'Success'}

        @self.app.get('/control_plane/get_autoscaler_query_interval')
        def get_autoscaler_query_interval():
            if isinstance(self.autoscaler, autoscalers.RequestRateAutoscaler):
                return {'query_interval': self.autoscaler.get_query_interval()}
            return {'query_interval': None}

        @self.app.get('/control_plane/get_ready_replicas')
        def get_ready_replicas():
            return {'ready_replicas': self.infra_provider.get_ready_replicas()}

        @self.app.get('/control_plane/get_latest_info')
        def get_latest_info():
            latest_info = {
                'replica_info':
                    self.infra_provider.get_replica_info(verbose=True),
                'uptime': self.infra_provider.get_uptime(),
            }
            latest_info = {
                k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
                for k, v in latest_info.items()
            }
            return latest_info

        @self.app.post('/control_plane/terminate')
        def terminate(request: fastapi.Request):
            del request
            logger.info('Terminating service...')
            if self.autoscaler is not None:
                logger.info('Terminate autoscaler...')
                self.autoscaler.terminate()
            msg = self.infra_provider.terminate()
            # Cleanup cloud storage
            # TODO(tian): move to local serve_down so that we can cleanup
            # local storage cache as well.
            task = sky.Task.from_yaml(self.task_yaml)
            backend = backends.CloudVmRayBackend()
            backend.teardown_ephemeral_storage(task)
            return {'message': msg}

        # Run replica_prober and autoscaler (if autoscaler is defined)
        # in separate threads in the background.
        # This should not block the main thread.
        self.infra_provider.start_replica_prober()
        if self.autoscaler is not None:
            self.autoscaler.start()

        # Disable all GET logs if SKYPILOT_DEBUG is not set to avoid overflood
        # the control plane logs.
        if not env_options.Options.SHOW_DEBUG_INFO.get():
            logging.getLogger('uvicorn.access').addFilter(
                SuppressSuccessGetAccessLogsFilter())

        logger.info(
            f'SkyServe Control Plane started on http://localhost:{self.port}')
        uvicorn.run(self.app, host='localhost', port=self.port)


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
    service_spec = serve.SkyServiceSpec.from_yaml(args.task_yaml)
    _infra_provider = infra_providers.SkyPilotInfraProvider(
        args.task_yaml,
        args.service_name,
        readiness_suffix=service_spec.readiness_suffix,
        initial_delay_seconds=service_spec.initial_delay_seconds,
        post_data=service_spec.post_data)

    # ======= Autoscaler =========
    _autoscaler = autoscalers.RequestRateAutoscaler(
        _infra_provider,
        frequency=20,
        min_nodes=service_spec.min_replicas,
        max_nodes=service_spec.max_replicas,
        upper_threshold=service_spec.qps_upper_threshold,
        lower_threshold=service_spec.qps_lower_threshold,
        cooldown=60,
        query_interval=60)

    # ======= ControlPlane =========
    control_plane = ControlPlane(args.port, args.task_yaml, _infra_provider,
                                 _autoscaler)
    control_plane.run()

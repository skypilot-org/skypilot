"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import argparse
import base64
import logging
import pickle
from typing import Optional

import fastapi
import uvicorn

from sky import authentication
from sky import serve
from sky import sky_logging
from sky.serve import autoscalers
from sky.serve import infra_providers
from sky.utils import env_options

# Use the explicit logger name so that the logger is under the
# `sky.serve.controller` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.serve.controller')


class SuppressSuccessGetAccessLogsFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not ('GET' in message and '200' in message)


class SkyServeController:
    """SkyServeController: control everything about replica.

    This class is responsible for:
        - Starting and terminating the replica monitor and autoscaler.
        - Providing the HTTP Server API for SkyServe to communicate with.
    """

    def __init__(self,
                 port: int,
                 infra_provider: infra_providers.InfraProvider,
                 autoscaler: Optional[autoscalers.Autoscaler] = None) -> None:
        self.port = port
        self.infra_provider = infra_provider
        self.autoscaler = autoscaler
        self.app = fastapi.FastAPI()

    def run(self) -> None:

        @self.app.post('/controller/update_num_requests')
        async def update_num_requests(request: fastapi.Request):
            # await request
            request_data = await request.json()
            # get request data
            num_requests = request_data['num_requests']
            logger.info(f'Received request: {request_data}')
            if isinstance(self.autoscaler, autoscalers.RequestRateAutoscaler):
                self.autoscaler.set_num_requests(num_requests)
            return {'message': 'Success'}

        @self.app.get('/controller/get_autoscaler_query_interval')
        def get_autoscaler_query_interval():
            if isinstance(self.autoscaler, autoscalers.RequestRateAutoscaler):
                return {'query_interval': self.autoscaler.get_query_interval()}
            return {'query_interval': None}

        @self.app.get('/controller/get_ready_replicas')
        def get_ready_replicas():
            return {'ready_replicas': self.infra_provider.get_ready_replicas()}

        @self.app.get('/controller/get_latest_info')
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

        @self.app.post('/controller/terminate')
        def terminate(request: fastapi.Request):
            del request
            logger.info('Terminating service...')
            if self.autoscaler is not None:
                logger.info('Terminate autoscaler...')
                self.autoscaler.terminate()
            msg = self.infra_provider.terminate()
            return {'message': msg}

        # Run replica_prober and autoscaler (if autoscaler is defined)
        # in separate threads in the background.
        # This should not block the main thread.
        self.infra_provider.start_replica_prober()
        if self.autoscaler is not None:
            self.autoscaler.start()

        # Disable all GET logs if SKYPILOT_DEBUG is not set to avoid overflowing
        # the controller logs.
        if not env_options.Options.SHOW_DEBUG_INFO.get():
            logging.getLogger('uvicorn.access').addFilter(
                SuppressSuccessGetAccessLogsFilter())

        logger.info(
            f'SkyServe Controller started on http://localhost:{self.port}')
        uvicorn.run(self.app, host='localhost', port=self.port)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe Controller')
    parser.add_argument('--service-name',
                        type=str,
                        help='Name of the service',
                        required=True)
    parser.add_argument('--task-yaml',
                        type=str,
                        help='Task YAML file',
                        required=True)
    parser.add_argument('--controller-port',
                        type=int,
                        help='Port to run the controller',
                        required=True)
    args = parser.parse_args()

    # Generate ssh key pair to avoid race condition when multiple sky.launch
    # are executed at the same time.
    authentication.get_or_generate_keys()

    # ======= Infra Provider =========
    service_spec = serve.SkyServiceSpec.from_yaml(args.task_yaml)
    _infra_provider = infra_providers.SkyPilotInfraProvider(
        args.task_yaml,
        args.service_name,
        controller_port=args.controller_port,
        readiness_suffix=service_spec.readiness_suffix,
        initial_delay_seconds=service_spec.initial_delay_seconds,
        post_data=service_spec.post_data)

    # ======= Autoscaler =========
    _autoscaler = autoscalers.RequestRateAutoscaler(
        _infra_provider,
        auto_restart=service_spec.auto_restart,
        frequency=20,
        min_nodes=service_spec.min_replicas,
        max_nodes=service_spec.max_replicas,
        upper_threshold=service_spec.qps_upper_threshold,
        lower_threshold=service_spec.qps_lower_threshold,
        cooldown=60,
        query_interval=60)

    # ======= SkyServeController =========
    controller = SkyServeController(args.controller_port, _infra_provider,
                                    _autoscaler)
    controller.run()

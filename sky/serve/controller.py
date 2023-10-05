"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import argparse
import asyncio
import base64
import logging
import os
import pickle
import signal
import threading
import time
from typing import Optional

import fastapi
import uvicorn

from sky import authentication
from sky import serve
from sky import sky_logging
from sky import status_lib
from sky.serve import autoscalers
from sky.serve import infra_providers
from sky.serve import serve_state
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
        self.terminating = False
        self.load_balancer_received_terminal_signal = False
        self.app = fastapi.FastAPI()

    def _check_terminate(self):
        while True:
            if self.terminating and self.load_balancer_received_terminal_signal:
                # 1s grace period for the rare case that terminate is set but
                # return of /terminate request is not ready yet.
                time.sleep(1)
                logger.info('Terminate controller...')
                os.kill(os.getpid(), signal.SIGINT)
            time.sleep(10)

    def run(self) -> None:

        @self.app.post('/controller/update_num_requests')
        def update_num_requests(request: fastapi.Request):
            # await request
            request_data = asyncio.run(request.json())
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

        @self.app.get('/controller/is_terminating')
        def is_terminating():
            if self.terminating:
                self.load_balancer_received_terminal_signal = True
            return {'is_terminating': self.terminating}

        @self.app.get('/controller/get_latest_info')
        def get_latest_info():
            # NOTE(dev): Keep this align with
            # sky.backends.backend_utils._add_default_value_to_local_record
            record = serve_state.get_service_from_name(
                self.infra_provider.service_name)
            if record is None:
                record = {}
            latest_info = {
                'replica_info':
                    self.infra_provider.get_replica_info(verbose=True),
                'uptime': record.get('uptime', None),
                'status': record.get('status',
                                     status_lib.ServiceStatus.UNKNOWN),
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
            serve_state.set_status(self.infra_provider.service_name,
                                   status_lib.ServiceStatus.SHUTTING_DOWN)
            if self.autoscaler is not None:
                logger.info('Terminate autoscaler...')
                self.autoscaler.terminate()
            msg = self.infra_provider.terminate()
            if msg is None:
                # We cannot terminate the controller now because we still
                # need the output of this request to be sent back.
                self.terminating = True
            return {'message': msg}

        # Run replica_prober and autoscaler (if autoscaler is defined)
        # in separate threads in the background.
        # This should not block the main thread.
        self.infra_provider.start_replica_prober()
        if self.autoscaler is not None:
            self.autoscaler.start()

        # Start a daemon to check if the controller is terminating, and if so,
        # shutdown the controller so the skypilot jobs will finish, thus enable
        # the controller VM to autostop.
        terminate_checking_daemon = threading.Thread(
            target=self._check_terminate, daemon=True)
        terminate_checking_daemon.start()

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

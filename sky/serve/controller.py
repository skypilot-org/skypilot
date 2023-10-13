"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import argparse
import asyncio
import base64
import logging
import pickle
import threading
import time

import fastapi
import uvicorn

from sky import authentication
from sky import serve
from sky import sky_logging
from sky.serve import autoscalers
from sky.serve import constants
from sky.serve import infra_providers
from sky.serve import serve_state
from sky.serve import serve_utils
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

    def __init__(self, port: int, infra_provider: infra_providers.InfraProvider,
                 autoscaler: autoscalers.Autoscaler) -> None:
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
                # TODO(tian): Directly kill all threads and cleanup using db
                # record, instead of waiting the threads to receive signal.
                serve_utils.kill_children_and_self_processes()
            time.sleep(10)

    def _run_autoscaler(self):
        logger.info('Starting autoscaler monitor.')
        while True:
            try:
                replica_info = self.infra_provider.get_replica_info(
                    verbose=env_options.Options.SHOW_DEBUG_INFO.get())
                logger.info(f'All replica info: {replica_info}')
                scaling_option = self.autoscaler.evaluate_scaling(replica_info)
                if (scaling_option.operator ==
                        autoscalers.AutoscalerDecisionOperator.SCALE_UP):
                    assert isinstance(scaling_option.target,
                                      int), scaling_option
                    self.infra_provider.scale_up(scaling_option.target)
                elif (scaling_option.operator ==
                      autoscalers.AutoscalerDecisionOperator.SCALE_DOWN):
                    assert isinstance(scaling_option.target,
                                      list), scaling_option
                    self.infra_provider.scale_down(scaling_option.target)
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error(f'Error in autoscaler: {e}')
            for _ in range(self.autoscaler.frequency):
                if self.autoscaler_stop_event.is_set():
                    logger.info('Autoscaler monitor terminated.')
                    return
                time.sleep(1)

    def _start_autoscaler(self):
        self.autoscaler_stop_event = threading.Event()
        self.autoscaler_thread = threading.Thread(target=self._run_autoscaler)
        self.autoscaler_thread.start()

    def _terminate_autoscaler(self):
        self.autoscaler_stop_event.set()
        self.autoscaler_thread.join()

    def run(self) -> None:

        @self.app.post('/controller/report_request_information')
        def report_request_information(request: fastapi.Request):
            request_data = asyncio.run(request.json())
            request_information_payload = request_data.get(
                'request_information')
            request_information = pickle.loads(
                base64.b64decode(request_information_payload))
            logger.info(
                f'Received request information: {request_information!r}')
            if isinstance(self.autoscaler, autoscalers.RequestRateAutoscaler):
                if not isinstance(request_information,
                                  serve_utils.RequestTimestamp):
                    raise ValueError('Request information must be of type '
                                     'serve_utils.RequestTimestamp for '
                                     'RequestRateAutoscaler.')
                self.autoscaler.update_request_information(request_information)
            return {'message': 'Success'}

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
                                     serve_state.ServiceStatus.UNKNOWN),
                'policy': self.autoscaler.policy_str,
                'auto_restart': self.autoscaler.auto_restart,
                'requested_resources':
                    self.infra_provider.get_requested_resources(),
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
            serve_state.set_service_status(
                self.infra_provider.service_name,
                serve_state.ServiceStatus.SHUTTING_DOWN)
            logger.info('Terminate autoscaler...')
            self._terminate_autoscaler()
            msg = self.infra_provider.terminate()
            if msg is None:
                # We cannot terminate the controller now because we still
                # need the output of this request to be sent back.
                self.terminating = True
            return {'message': msg}

        self._start_autoscaler()

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
        args.service_name, service_spec, task_yaml_path=args.task_yaml)

    # ======= Autoscaler =========
    _autoscaler = autoscalers.RequestRateAutoscaler(
        service_spec,
        frequency=constants.AUTOSCALER_SCALE_FREQUENCY,
        cooldown=constants.AUTOSCALER_COOLDOWN_SECONDS,
        rps_window_size=constants.AUTOSCALER_RPS_WINDOW_SIZE)

    # ======= SkyServeController =========
    controller = SkyServeController(args.controller_port, _infra_provider,
                                    _autoscaler)
    controller.run()

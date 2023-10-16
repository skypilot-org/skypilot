"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import asyncio
import base64
import logging
import pickle
import threading
import time

import fastapi
import uvicorn

from sky import serve
from sky import sky_logging
from sky.serve import autoscalers
from sky.serve import constants
from sky.serve import infra_providers
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

    def __init__(self, service_name: str, service_spec: serve.SkyServiceSpec,
                 task_yaml: str, port: int) -> None:
        self.service_name = service_name
        self.infra_provider: infra_providers.InfraProvider = (
            infra_providers.SkyPilotInfraProvider(service_name,
                                                  service_spec,
                                                  task_yaml_path=task_yaml))
        self.autoscaler: autoscalers.Autoscaler = (
            autoscalers.RequestRateAutoscaler(
                service_spec,
                frequency=constants.AUTOSCALER_SCALE_FREQUENCY,
                cooldown=constants.AUTOSCALER_COOLDOWN_SECONDS,
                rps_window_size=constants.AUTOSCALER_RPS_WINDOW_SIZE))
        self.port = port
        self.app = fastapi.FastAPI()

    def _run_autoscaler(self):
        logger.info('Starting autoscaler monitor.')
        while True:
            try:
                replica_info = serve_utils.get_replica_info(
                    self.service_name,
                    with_handle=env_options.Options.SHOW_DEBUG_INFO.get())
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
            time.sleep(self.autoscaler.frequency)

    def run(self) -> None:

        @self.app.post('/controller/load_balancer_sync')
        def load_balancer_sync(request: fastapi.Request):
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
            return {'ready_replicas': self.infra_provider.get_ready_replicas()}

        threading.Thread(target=self._run_autoscaler).start()

        # Disable all GET logs if SKYPILOT_DEBUG is not set to avoid overflowing
        # the controller logs.
        if not env_options.Options.SHOW_DEBUG_INFO.get():
            logging.getLogger('uvicorn.access').addFilter(
                SuppressSuccessGetAccessLogsFilter())

        logger.info(
            f'SkyServe Controller started on http://localhost:{self.port}')
        uvicorn.run(self.app, host='localhost', port=self.port)


def run_controller(service_name: str, service_spec: serve.SkyServiceSpec,
                   task_yaml: str, controller_port: int):
    controller = SkyServeController(service_name, service_spec, task_yaml,
                                    controller_port)
    controller.run()

"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
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
from sky.serve import replica_managers
from sky.serve import serve_utils
from sky.utils import env_options

logger = sky_logging.init_logger(__name__)


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
        self.replica_manager: replica_managers.ReplicaManager = (
            replica_managers.SkyPilotReplicaManager(service_name=service_name,
                                                    spec=service_spec,
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
                    self.replica_manager.scale_up(scaling_option.target)
                elif (scaling_option.operator ==
                      autoscalers.AutoscalerDecisionOperator.SCALE_DOWN):
                    assert isinstance(scaling_option.target,
                                      list), scaling_option
                    self.replica_manager.scale_down(scaling_option.target)
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error(f'Error in autoscaler: {e}')
            time.sleep(self.autoscaler.frequency)

    def run(self) -> None:

        @self.app.post('/controller/load_balancer_sync')
        async def load_balancer_sync(request: fastapi.Request):
            request_data = await request.json()
            request_information_payload = request_data.get(
                'request_information')
            request_information = pickle.loads(
                base64.b64decode(request_information_payload))
            logger.info(
                f'Received inflight request information: {request_information}')
            if isinstance(self.autoscaler, autoscalers.RequestRateAutoscaler):
                if not isinstance(request_information,
                                  serve_utils.RequestTimestamp):
                    raise ValueError('Request information must be of type '
                                     'serve_utils.RequestTimestamp for '
                                     'RequestRateAutoscaler.')
                self.autoscaler.update_request_information(request_information)
            return {
                'ready_replica_ips':
                    self.replica_manager.get_ready_replica_ips()
            }

        @self.app.on_event('startup')
        def configure_logger():
            uvicorn_access_logger = logging.getLogger('uvicorn.access')
            for handler in uvicorn_access_logger.handlers:
                handler.setFormatter(sky_logging.FORMATTER)

        threading.Thread(target=self._run_autoscaler).start()

        logger.info('SkyServe Controller started on '
                    f'http://localhost:{self.port}')

        uvicorn.run(self.app, host='localhost', port=self.port)


def run_controller(service_name: str, service_spec: serve.SkyServiceSpec,
                   task_yaml: str, controller_port: int):
    controller = SkyServeController(service_name, service_spec, task_yaml,
                                    controller_port)
    controller.run()

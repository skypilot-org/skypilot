"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import logging
import threading
import time
import traceback
from typing import Any, Dict, List

import colorama
import fastapi
import uvicorn

from sky import global_user_state
from sky import serve
from sky import sky_logging
from sky.serve import autoscalers
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.utils import common_utils
from sky.utils import ux_utils

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
                 task_yaml: str, host: str, port: int) -> None:
        self._service_name = service_name
        self._replica_manager: replica_managers.ReplicaManager = (
            replica_managers.SkyPilotReplicaManager(service_name=service_name,
                                                    spec=service_spec,
                                                    task_yaml_path=task_yaml))
        self._autoscaler: autoscalers.Autoscaler = (
            autoscalers.Autoscaler.from_spec(service_name, service_spec))
        self._host = host
        self._port = port
        self._app = fastapi.FastAPI()

    def _run_autoscaler(self):
        logger.info('Starting autoscaler.')
        while True:
            try:
                replica_infos = serve_state.get_replica_infos(
                    self._service_name)
                logger.info(f'All replica info: {replica_infos}')
                scaling_options = self._autoscaler.evaluate_scaling(
                    replica_infos)
                for scaling_option in scaling_options:
                    logger.info(f'Scaling option received: {scaling_option}')
                    if (scaling_option.operator ==
                            autoscalers.AutoscalerDecisionOperator.SCALE_UP):
                        assert (scaling_option.target is None or isinstance(
                            scaling_option.target, dict)), scaling_option
                        self._replica_manager.scale_up(scaling_option.target)
                    elif (scaling_option.operator ==
                          autoscalers.AutoscalerDecisionOperator.SCALE_DOWN):
                        assert isinstance(scaling_option.target,
                                          int), scaling_option
                        self._replica_manager.scale_down(scaling_option.target)
                    else:
                        with ux_utils.enable_traceback():
                            logger.error('Error in scaling_option.operator: '
                                         f'{scaling_option.operator}')
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error('Error in autoscaler: '
                             f'{common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback: {traceback.format_exc()}')
            time.sleep(self._autoscaler.get_decision_interval())

    def _purge_replica(self, replica_id: int) -> Dict[str, str]:
        logger.info(f'Purging replica {replica_id}...')
        replica_info = serve_state.get_replica_info_from_id(
            self._service_name, replica_id)
        assert replica_info is not None
        replica_cluster_is_remaining = False
        if replica_info.status in serve_state.ReplicaStatus.failed_statuses():
            if global_user_state.get_cluster_from_name(
                    replica_info.cluster_name) is not None:
                replica_cluster_is_remaining = True
            serve_state.remove_replica(self._service_name, replica_id)
            if replica_cluster_is_remaining:
                return {
                    'message':
                        f'{colorama.Fore.YELLOW}Purged replica {replica_id} '
                        f'with failed status ({replica_info.status}). This may'
                        f' indicate a resource leak. Please check the following'
                        f' SkyPilot cluster on the controller: '
                        f'{replica_info.cluster_name}{colorama.Style.RESET_ALL}'
                }
            else:
                return {
                    'message': f'Successfully purged replica '
                               f'{replica_id}'
                }
        else:
            return {
                'message': f'No purging for replica {replica_id} since '
                           f'the replica does not have a failed status.'
            }

    def run(self) -> None:

        @self._app.post('/controller/load_balancer_sync')
        async def load_balancer_sync(request: fastapi.Request):
            request_data = await request.json()
            # TODO(MaoZiming): Check aggregator type.
            request_aggregator: Dict[str, Any] = request_data.get(
                'request_aggregator', {})
            timestamps: List[int] = request_aggregator.get('timestamps', [])
            logger.info(f'Received {len(timestamps)} inflight requests.')
            self._autoscaler.collect_request_information(request_aggregator)
            return {
                'ready_replica_urls':
                    self._replica_manager.get_active_replica_urls()
            }

        @self._app.post('/controller/update_service')
        async def update_service(request: fastapi.Request):
            request_data = await request.json()
            try:
                version = request_data.get('version', None)
                if version is None:
                    return {'message': 'Error: version is not specified.'}
                update_mode_str = request_data.get(
                    'mode', serve_utils.DEFAULT_UPDATE_MODE.value)
                update_mode = serve_utils.UpdateMode(update_mode_str)
                logger.info(f'Update to new version {version} with '
                            f'update_mode {update_mode}.')
                # The yaml with the name latest_task_yaml will be synced
                # See sky/serve/core.py::update
                latest_task_yaml = serve_utils.generate_task_yaml_file_name(
                    self._service_name, version)
                service = serve.SkyServiceSpec.from_yaml(latest_task_yaml)
                logger.info(
                    f'Update to new version version {version}: {service}')

                self._replica_manager.update_version(version,
                                                     service,
                                                     update_mode=update_mode)
                new_autoscaler = autoscalers.Autoscaler.from_spec(
                    self._service_name, service)
                if not isinstance(self._autoscaler, type(new_autoscaler)):
                    logger.info('Autoscaler type changed to '
                                f'{type(new_autoscaler)}, updating autoscaler.')
                    old_autoscaler = self._autoscaler
                    self._autoscaler = new_autoscaler
                    self._autoscaler.load_dynamic_states(
                        old_autoscaler.dump_dynamic_states())
                self._autoscaler.update_version(version,
                                                service,
                                                update_mode=update_mode)
                return {'message': 'Success'}
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in update_service: '
                             f'{common_utils.format_exception(e)}')
                return {'message': 'Error'}

        @self._app.post('/controller/terminate_replica')
        async def terminate_replica(request: fastapi.Request):
            request_data = await request.json()
            try:
                replica_id = request_data.get('replica_id')
                if replica_id is None:
                    return {'message': 'Error: replica ID is not specified.'}
                purge = request_data.get('purge')
                if purge is None:
                    return {'message': 'Error: purge is not specified.'}
                replica_info = serve_state.get_replica_info_from_id(
                    self._service_name, replica_id)
                if replica_info is None:
                    return {'message': f'Error: replica {replica_id} does not exist.'}
                
                if replica_info.status in serve_state.ReplicaStatus.failed_statuses():
                    if purge:
                        return self._purge_replica(replica_id)
                    else:
                        return {
                            'message': f'Error: replica {replica_id} has failed. '
                            f'Please use purge to remove the failed replica.'
                        }
                else:
                    if purge:
                        logger.info(f'Purging replica {replica_id}...')
                        self._replica_manager.scale_down(replica_id)
                        serve_state.remove_replica(self._service_name, replica_id)
                        return {'message': f'Successfully purged replica {replica_id}.'}
                    else:
                        logger.info(f'Terminating replica {replica_id}...')
                        self._replica_manager.scale_down(replica_id)
                        return {
                            'message': f'Success terminating replica {replica_id}.'
                        }

            except Exception as e:  # pylint: disable=broad-except
                error_message = (f'Error in terminate_replica: '
                                 f'{common_utils.format_exception(e)}')
                logger.error(error_message)
                return {'message': error_message}

        @self._app.on_event('startup')
        def configure_logger():
            uvicorn_access_logger = logging.getLogger('uvicorn.access')
            for handler in uvicorn_access_logger.handlers:
                handler.setFormatter(sky_logging.FORMATTER)

        threading.Thread(target=self._run_autoscaler).start()

        logger.info('SkyServe Controller started on '
                    f'http://{self._host}:{self._port}')

        uvicorn.run(self._app, host={self._host}, port=self._port)


# TODO(tian): Probably we should support service that will stop the VM in
# specific time period.
def run_controller(service_name: str, service_spec: serve.SkyServiceSpec,
                   task_yaml: str, controller_host: str, controller_port: int):
    controller = SkyServeController(service_name, service_spec, task_yaml,
                                    controller_host, controller_port)
    controller.run()

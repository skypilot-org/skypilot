"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import logging
import threading
import time
import traceback
from typing import List

import fastapi
import uvicorn

from sky import serve
from sky import sky_logging
from sky.serve import autoscalers
from sky.serve import constants
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.utils import common_utils
from sky.utils import env_options
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
                 task_yaml: str, port: int) -> None:
        self._service_name = service_name
        self._replica_manager: replica_managers.ReplicaManager = (
            replica_managers.SkyPilotReplicaManager(service_name=service_name,
                                                    spec=service_spec,
                                                    task_yaml_path=task_yaml))
        self._autoscaler: autoscalers.Autoscaler = (
            autoscalers.HeteroGPUAutoscaler(
                service_spec,
                qps_window_size=constants.AUTOSCALER_QPS_WINDOW_SIZE_SECONDS))
        self._port = port
        self._app = fastapi.FastAPI()

    def _run_autoscaler(self):
        logger.info('Starting autoscaler.')
        while True:
            try:
                replica_info = serve_state.get_replica_infos(self._service_name)
                replica_info_dicts = [
                    info.to_info_dict(
                        with_handle=env_options.Options.SHOW_DEBUG_INFO.get())
                    for info in replica_info
                ]
                logger.info(f'All replica info: {replica_info_dicts}')
                scaling_options = self._autoscaler.evaluate_scaling(
                    replica_info)
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
            time.sleep(constants.AUTOSCALER_DEFAULT_DECISION_INTERVAL_SECONDS)

    def _run_heteroGPU_autoscaler(self):
        logger.info('Starting autoscaler.')
        while True:
            try:
                # If the primary replica successfully launched to ReplicaStatus.READY,
                # then we terminate the corresponding fallback replicas. Returns a list
                # of SCALE_DOWN decisions
                self._autoscaler.fallback_scale_down_sync(
                    self._service_name, self._replica_manager)
                # Making autoscaling decisions based on the allocation
                # of the solver
                replica_info = serve_state.get_replica_infos(self._service_name)
                replica_info_dicts = [
                    info.to_info_dict(
                        with_handle=env_options.Options.SHOW_DEBUG_INFO.get())
                    for info in replica_info
                ]
                logger.info(
                    f'All replica info after fallback sync: {replica_info_dicts}'
                )
                scaling_decisions = self._autoscaler.evaluate_scaling(
                    replica_info)

                for scaling_decision in scaling_decisions:
                    # When we want to launch multiple fallback replicas with
                    # the primary replica.
                    if isinstance(scaling_decision, list):
                        # Distinguish fallback decisions and primary decision
                        primary_decision: autoscalers.AutoscalerDecision
                        fallback_decisions: List[
                            autoscalers.AutoscalerDecision] = []
                        for decision in scaling_decision:
                            if decision.target['is_fallback']:
                                fallback_decisions.append(decision)
                            else:
                                primary_decision = decision
                        # launch all the fallback decision first and get a list of replica id
                        fallback_replica_id_list: List[int] = []
                        for fallback_decision in fallback_decisions:
                            assert (fallback_decision.operator == autoscalers.
                                    AutoscalerDecisionOperator.SCALE_UP,
                                    fallback_decision.operator)
                            replica_id = self._replica_manager.scale_up(
                                fallback_decision.target)
                            fallback_replica_id_list.append(replica_id)
                        # launch primary decision with fallback replica id list
                        primary_decision.target.update({
                            'fallback_replica_id_list': fallback_replica_id_list
                        })
                        self._replica_manager.scale_up(primary_decision.target)
                    elif (scaling_decision.operator ==
                          autoscalers.AutoscalerDecisionOperator.SCALE_UP):
                        assert isinstance(scaling_decision.target,
                                          dict), scaling_decision
                        self._replica_manager.scale_up(scaling_decision.target)
                    elif (scaling_decision.operator ==
                          autoscalers.AutoscalerDecisionOperator.SCALE_DOWN):
                        assert isinstance(scaling_decision.target,
                                          int), scaling_decision
                        self._replica_manager.scale_down(
                            scaling_decision.target)
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error('Error in autoscaler: '
                             f'{common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback: {traceback.format_exc()}')
            time.sleep(self._autoscaler.frequency)

    def run(self) -> None:

        @self._app.post('/controller/load_balancer_sync')
        async def load_balancer_sync(request: fastapi.Request):
            request_data = await request.json()
            request_aggregator = request_data.get('request_aggregator')
            logger.info(
                f'Received inflight request information: {request_aggregator}')
            self._autoscaler.collect_request_information(request_aggregator)
            return {
                'ready_replica_urls_accels':
                    self._replica_manager.get_ready_replica_urls_with_accelerator(),
                'ilp_assignment_vector':
                    self._autoscaler.get_ilp_assignment_vector()
            }

        @self._app.on_event('startup')
        def configure_logger():
            uvicorn_access_logger = logging.getLogger('uvicorn.access')
            for handler in uvicorn_access_logger.handlers:
                handler.setFormatter(sky_logging.FORMATTER)

        #threading.Thread(target=self._run_autoscaler).start()
        threading.Thread(target=self._run_heteroGPU_autoscaler).start()

        logger.info('SkyServe Controller started on '
                    f'http://localhost:{self._port}')

        uvicorn.run(self._app, host='localhost', port=self._port)


# TODO(tian): Probably we should support service that will stop the VM in
# specific time period.
def run_controller(service_name: str, service_spec: serve.SkyServiceSpec,
                   task_yaml: str, controller_port: int):
    controller = SkyServeController(service_name, service_spec, task_yaml,
                                    controller_port)
    controller.run()

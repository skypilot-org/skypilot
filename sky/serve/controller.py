"""SkyServeController: the central controller of SkyServe.

Responsible for autoscaling and replica management.
"""
import contextlib
import copy
import logging
import multiprocessing
import os
import threading
import time
import traceback
from typing import Any, Dict, List, Optional

import colorama
import fastapi
from fastapi import responses
import uvicorn

from sky import global_user_state
from sky import serve
from sky import sky_logging
from sky.serve import autoscalers
from sky.serve import constants
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants as skylet_constants
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import registry
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


class SuppressSuccessGetAccessLogsFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not ('GET' in message and '200' in message)


def _get_lb_j2_vars(controller_addr: str, lb_port: int, lb_region: str,
                    lb_policy: str, meta_lb_policy: str,
                    max_concurrent_requests: int) -> Dict[str, Any]:
    return {
        'load_balancer_port': lb_port,
        'controller_addr': controller_addr,
        'sky_activate_python_env':
            skylet_constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV,
        'lb_envs': controller_utils.sky_managed_cluster_envs(),
        'region': lb_region,
        'load_balancing_policy': lb_policy,
        'meta_lb_policy': meta_lb_policy,
        'max_concurrent_requests': max_concurrent_requests,
    }


class SkyServeController:
    """SkyServeController: control everything about replica.

    This class is responsible for:
        - Starting and terminating the replica monitor and autoscaler.
        - Providing the HTTP Server API for SkyServe to communicate with.
    """

    def _get_latest_service_spec(self) -> serve.SkyServiceSpec:
        version = self._replica_manager.latest_version
        assert isinstance(self._replica_manager,
                          replica_managers.SkyPilotReplicaManager)
        return self._replica_manager.get_version_spec(version)

    def _terminate_all_lb_replicas(self) -> None:
        ss = self._get_latest_service_spec()
        lb_svc_name = serve_utils.format_lb_service_name(self._service_name)
        lb_replicas = serve_state.get_replica_infos(lb_svc_name)
        hosted_zone = ss.route53_hosted_zone
        change_batch = []
        for info in lb_replicas:
            cn = info.cluster_name
            lb_record = global_user_state.get_cluster_from_name(cn)
            if lb_record is not None:
                lb_region = lb_record['handle'].launched_resources.region
                lb_ip = serve_utils.get_cluster_ip(cn)
                if lb_ip is not None:
                    # Hosted zone is needed for Route53 cleanup. If missing,
                    # skip DNS record deletion but continue terminating LB.
                    if (hosted_zone is None or
                            ss.target_hosted_zone_id is None):
                        logger.warning(
                            'Skipping Route53 deletion for external LB %s: '
                            'hosted_zone / target_hosted_zone_id not set.',
                            cn)
                    else:
                        change_batch.append(
                            serve_utils.get_route53_change('DELETE',
                                                           self._service_name,
                                                           hosted_zone, 'A',
                                                           lb_region, lb_ip))
            assert self._lb_replica_manager is not None
            self._lb_replica_manager.scale_down(info.replica_id,
                                                purge=True,
                                                drain_seconds=0)
        serve_utils.apply_change_batch(change_batch, self._service_name,
                                       ss.target_hosted_zone_id, logger)

    def _launch_lb_replicas(self) -> None:
        ss = self._get_latest_service_spec()
        if ss.external_load_balancers is None:
            return
        rids = []
        for lb_config in ss.external_load_balancers:
            j2_vars = _get_lb_j2_vars(
                self._controller_addr,
                constants.EXTERNAL_LB_PORT,
                lb_config['resources']['region'],
                lb_config['load_balancing_policy'],
                ss.load_balancing_policy,
                # TODO(tian): Constant for default.
                ss.max_concurrent_requests or 10)
            rc = copy.deepcopy(lb_config['resources'])
            if 'cloud' in rc:
                rc['cloud'] = registry.CLOUD_REGISTRY.from_str(rc['cloud'])
            assert self._lb_replica_manager is not None
            rid = self._lb_replica_manager.scale_up(rc, j2_vars)
            rids.append(rid)
        p = multiprocessing.Process(
            target=serve_utils.wait_external_load_balancers,
            args=(self._service_name, ss, logger))
        self._procs[str(rids)] = p
        p.start()

    def __init__(self, service_name: str, service_spec: serve.SkyServiceSpec,
                 task_yaml: str, host: str, port: int) -> None:
        self._procs: Dict[str, multiprocessing.Process] = {}
        self._service_name = service_name
        self._service_spec = service_spec
        external_host = serve_utils.get_external_host()
        self._controller_addr = f'http://{external_host}:{port}'
        self._replica_manager: replica_managers.ReplicaManager = (
            replica_managers.SkyPilotReplicaManager(service_name=service_name,
                                                    spec=service_spec,
                                                    task_yaml_path=task_yaml))
        self._lb_replica_manager: Optional[
            replica_managers.ReplicaManager] = None
        if service_spec.external_load_balancers is not None:
            lb_svc_name = serve_utils.format_lb_service_name(service_name)
            # TODO(tian): Fix it by not introducing new service.
            serve_state.add_service(lb_svc_name, 0, '', '', '',
                                    serve_state.ServiceStatus.CONTROLLER_INIT,
                                    True)
            service_dir = os.path.expanduser(
                serve_utils.generate_remote_service_dir_name(lb_svc_name))
            os.makedirs(service_dir, exist_ok=True)
            lb_service_spec = copy.deepcopy(service_spec)
            # NOTE(tian): Only readiness probe is used in replica manager. We
            # manually set it here. Please align with
            # sky/templates/sky-serve-external-load-balancer.yaml.j2
            # TODO(tian): Make it configurable.
            lb_service_spec._readiness_headers = None
            lb_service_spec._readiness_path = constants.LB_HEALTH_ENDPOINT
            lb_service_spec._readiness_timeout_seconds = 20
            lb_service_spec._initial_delay_seconds = 120
            lb_service_spec._post_data = None
            serve_state.add_or_update_version(lb_svc_name,
                                              constants.INITIAL_VERSION,
                                              lb_service_spec)
            self._lb_replica_manager = replica_managers.SkyPilotReplicaManager(
                service_name=lb_svc_name,
                spec=lb_service_spec,
                task_yaml_path=constants.EXTERNAL_LB_TEMPLATE)
            self._launch_lb_replicas()
        self._autoscaler: autoscalers.Autoscaler = (
            autoscalers.Autoscaler.from_spec(service_name, service_spec))
        self._host = host
        self._port = port
        self._app = fastapi.FastAPI(lifespan=self.lifespan)

    @contextlib.asynccontextmanager
    async def lifespan(self, _: fastapi.FastAPI):
        uvicorn_access_logger = logging.getLogger('uvicorn.access')
        for handler in uvicorn_access_logger.handlers:
            handler.setFormatter(sky_logging.FORMATTER)
        yield

    def _run_autoscaler(self):
        logger.info('Starting autoscaler.')
        while True:
            try:
                replica_infos = serve_state.get_replica_infos(
                    self._service_name)
                # Use the active versions set by replica manager to make
                # sure we only scale down the outdated replicas that are
                # not used by the load balancer.
                record = serve_state.get_service_from_name(self._service_name)
                assert record is not None, ('No service record found for '
                                            f'{self._service_name}')
                active_versions = record['active_versions']
                logger.info(f'All replica info: {replica_infos}')
                external_lb_infos = serve_state.get_replica_infos(
                    serve_utils.format_lb_service_name(self._service_name))
                logger.info('All external load balancer infos: '
                            f'{external_lb_infos}')
                scaling_options = self._autoscaler.generate_scaling_decisions(
                    replica_infos, active_versions)
                for scaling_option in scaling_options:
                    logger.info(f'Scaling option received: {scaling_option}')
                    if (scaling_option.operator ==
                            autoscalers.AutoscalerDecisionOperator.SCALE_UP):
                        assert (scaling_option.target is None or isinstance(
                            scaling_option.target, dict)), scaling_option
                        self._replica_manager.scale_up(scaling_option.target)
                    else:
                        assert isinstance(scaling_option.target,
                                          int), scaling_option
                        self._replica_manager.scale_down(scaling_option.target)
                for k, p in list(self._procs.items()):
                    if not p.is_alive():
                        logger.info(f'Wait LB Process {k} finished.')
                        del self._procs[k]
                        p.join()
            except Exception as e:  # pylint: disable=broad-except
                # No matter what error happens, we should keep the
                # monitor running.
                logger.error('Error in autoscaler: '
                             f'{common_utils.format_exception(e)}')
                with ux_utils.enable_traceback():
                    logger.error(f'  Traceback: {traceback.format_exc()}')
            time.sleep(self._autoscaler.get_decision_interval())

    def run(self) -> None:

        @self._app.post('/controller/load_balancer_sync')
        async def load_balancer_sync(
                request: fastapi.Request) -> fastapi.Response:
            request_data = await request.json()
            # TODO(MaoZiming): Check aggregator type.
            request_aggregator: Dict[str, Any] = request_data.get(
                'request_aggregator', {})
            timestamps: List[int] = request_aggregator.get('timestamps', [])
            logger.info(f'Received {len(timestamps)} inflight requests.')
            self._autoscaler.collect_request_information(request_aggregator)
            ready_lb_urls = None
            if self._lb_replica_manager is not None:
                ready_lb_urls = (
                    self._lb_replica_manager.get_active_replica_urls())
            return responses.JSONResponse(
                content={
                    'ready_replica_urls':
                        self._replica_manager.get_active_replica_urls(),
                    'ready_lb_urls': ready_lb_urls,
                },
                status_code=200,
            )

        @self._app.post('/controller/reload_lb_replicas')
        async def reload_lb_replicas(
                request: fastapi.Request) -> fastapi.Response:
            del request
            try:
                lb_svc_name = serve_utils.format_lb_service_name(
                    self._service_name)
                self._terminate_all_lb_replicas()
                # Wait for all replicas to goes to shutting down stage.
                while True:
                    lb_replicas = serve_state.get_replica_infos(lb_svc_name)
                    lb_replicas = [
                        info for info in lb_replicas if
                        info.status != serve_state.ReplicaStatus.SHUTTING_DOWN
                    ]
                    if not lb_replicas:
                        break
                    time.sleep(1)
                self._launch_lb_replicas()
            except Exception as e:  # pylint: disable=broad-except
                err = (f'Error in reload_lb_replicas: '
                       f'{common_utils.format_exception(e)}')
                logger.error(err)
                return responses.JSONResponse(content={'message': err},
                                              status_code=500)
            return responses.JSONResponse(content={'message': 'Success'},
                                          status_code=200)

        @self._app.post('/controller/update_service')
        async def update_service(request: fastapi.Request) -> fastapi.Response:
            request_data = await request.json()
            try:
                version = request_data.get('version', None)
                if version is None:
                    return responses.JSONResponse(
                        content={'message': 'Error: version is not specified.'},
                        status_code=400)
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
                return responses.JSONResponse(content={'message': 'Success'},
                                              status_code=200)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in update_service: '
                             f'{common_utils.format_exception(e)}')
                return responses.JSONResponse(content={'message': 'Error'},
                                              status_code=500)

        @self._app.post('/controller/terminate_replica')
        async def terminate_replica(
                request: fastapi.Request) -> fastapi.Response:
            request_data = await request.json()
            replica_id = request_data['replica_id']
            assert isinstance(replica_id,
                              int), 'Error: replica ID must be an integer.'
            purge = request_data['purge']
            assert isinstance(purge, bool), 'Error: purge must be a boolean.'
            replica_info = serve_state.get_replica_info_from_id(
                self._service_name, replica_id)
            assert replica_info is not None, (f'Error: replica '
                                              f'{replica_id} does not exist.')
            replica_status = replica_info.status

            if replica_status == serve_state.ReplicaStatus.SHUTTING_DOWN:
                return responses.JSONResponse(
                    status_code=409,
                    content={
                        'message':
                            f'Replica {replica_id} of service '
                            f'{self._service_name!r} is already in the process '
                            f'of terminating. Skip terminating now.'
                    })

            if (replica_status in serve_state.ReplicaStatus.failed_statuses()
                    and not purge):
                return responses.JSONResponse(
                    status_code=409,
                    content={
                        'message': f'{colorama.Fore.YELLOW}Replica '
                                   f'{replica_id} of service '
                                   f'{self._service_name!r} is in failed '
                                   f'status ({replica_info.status}). '
                                   f'Skipping its termination as it could '
                                   f'lead to a resource leak. '
                                   f'(Use `sky serve down '
                                   f'{self._service_name!r} --replica-id '
                                   f'{replica_id} --purge` to '
                                   'forcefully terminate the replica.)'
                                   f'{colorama.Style.RESET_ALL}'
                    })

            self._replica_manager.scale_down(replica_id, purge=purge)

            action = 'terminated' if not purge else 'purged'
            message = (f'{colorama.Fore.GREEN}Replica {replica_id} of service '
                       f'{self._service_name!r} is scheduled to be '
                       f'{action}.{colorama.Style.RESET_ALL}\n'
                       f'Please use {ux_utils.BOLD}sky serve status '
                       f'{self._service_name}{ux_utils.RESET_BOLD} '
                       f'to check the latest status.')
            return responses.JSONResponse(status_code=200,
                                          content={'message': message})

        @self._app.exception_handler(Exception)
        async def validation_exception_handler(
                request: fastapi.Request, exc: Exception) -> fastapi.Response:
            with ux_utils.enable_traceback():
                logger.error(f'Error in controller: {exc!r}')
            return responses.JSONResponse(
                status_code=500,
                content={
                    'message':
                        (f'Failed method {request.method} at URL {request.url}.'
                         f' Exception message is {exc!r}.')
                },
            )

        threading.Thread(target=self._run_autoscaler).start()

        logger.info('SkyServe Controller started on '
                    f'http://{self._host}:{self._port}')

        uvicorn.run(self._app, host=self._host, port=self._port)


# TODO(tian): Probably we should support service that will stop the VM in
# specific time period.
def run_controller(service_name: str, service_spec: serve.SkyServiceSpec,
                   task_yaml: str, controller_host: str, controller_port: int):
    controller = SkyServeController(service_name, service_spec, task_yaml,
                                    controller_host, controller_port)
    controller.run()

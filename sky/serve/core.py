"""SkyServe core APIs."""
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

import colorama

import sky
from sky import backends
from sky import exceptions
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds.service_catalog import common as service_catalog_common
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import admin_policy_utils
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def _validate_service_task(task: 'sky.Task') -> None:
    """Validate the task for Sky Serve.

    Args:
        task: sky.Task to validate

    Raises:
        ValueError: if the arguments are invalid.
        RuntimeError: if the task.serve is not found.
    """
    spot_resources: List['sky.Resources'] = [
        resource for resource in task.resources if resource.use_spot
    ]
    # TODO(MaoZiming): Allow mixed on-demand and spot specification in resources
    # On-demand fallback should go to the resources specified as on-demand.
    if len(spot_resources) not in [0, len(task.resources)]:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Resources must either all use spot or none use spot. '
                'To use on-demand and spot instances together, '
                'use `dynamic_ondemand_fallback` or set '
                'base_ondemand_fallback_replicas.')

    if task.service is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Service section not found.')

    policy_description = ('on-demand'
                          if task.service.dynamic_ondemand_fallback else 'spot')
    for resource in list(task.resources):
        if resource.job_recovery is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('job_recovery is disabled for SkyServe. '
                                 'SkyServe will replenish preempted spot '
                                 f'with {policy_description} instances.')

    replica_ingress_port: Optional[int] = None
    for requested_resources in task.resources:
        if (task.service.use_ondemand_fallback and
                not requested_resources.use_spot):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    '`use_ondemand_fallback` is only supported '
                    'for spot resources. Please explicitly specify '
                    '`use_spot: true` in resources for on-demand fallback.')
        requested_ports = list(
            resources_utils.port_ranges_to_set(requested_resources.ports))
        if len(requested_ports) != 1:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Must only specify one port in resources. Each replica '
                    'will use the port specified as application ingress port.')
        service_port = requested_ports[0]
        if replica_ingress_port is None:
            replica_ingress_port = service_port
        elif service_port != replica_ingress_port:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Got multiple ports: {service_port} and '
                    f'{replica_ingress_port} in different resources. '
                    'Please specify the same port instead.')


@usage_lib.entrypoint
def up(
    task: 'sky.Task',
    service_name: Optional[str] = None,
) -> Tuple[str, str]:
    """Spin up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.

    Returns:
        service_name: str; The name of the service.  Same if passed in as an
            argument.
        endpoint: str; The service endpoint.
    """
    if service_name is None:
        service_name = serve_utils.generate_service_name()

    # The service name will be used as:
    # 1. controller cluster name: 'sky-serve-controller-<service_name>'
    # 2. replica cluster name: '<service_name>-<replica_id>'
    # In both cases, service name shares the same regex with cluster name.
    if re.fullmatch(constants.CLUSTER_NAME_VALID_REGEX, service_name) is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Service name {service_name!r} is invalid: '
                             f'ensure it is fully matched by regex (e.g., '
                             'only contains lower letters, numbers and dash): '
                             f'{constants.CLUSTER_NAME_VALID_REGEX}')

    _validate_service_task(task)

    dag, mutated_user_config = admin_policy_utils.apply(
        task, use_mutated_config_in_current_request=False)
    task = dag.tasks[0]

    with rich_utils.safe_status(
            ux_utils.spinner_message('Initializing service')):
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task, path='serve')

    with tempfile.NamedTemporaryFile(
            prefix=f'service-task-{service_name}-',
            mode='w',
    ) as service_file, tempfile.NamedTemporaryFile(
            prefix=f'controller-task-{service_name}-',
            mode='w',
    ) as controller_file:
        controller_name = serve_utils.SKY_SERVE_CONTROLLER_NAME
        task_config = task.to_yaml_config()
        common_utils.dump_yaml(service_file.name, task_config)
        remote_tmp_task_yaml_path = (
            serve_utils.generate_remote_tmp_task_yaml_file_name(service_name))
        remote_config_yaml_path = (
            serve_utils.generate_remote_config_yaml_file_name(service_name))
        controller_log_file = (
            serve_utils.generate_remote_controller_log_file_name(service_name))
        controller_resources = controller_utils.get_controller_resources(
            controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
            task_resources=task.resources)

        vars_to_fill = {
            'remote_task_yaml_path': remote_tmp_task_yaml_path,
            'local_task_yaml_path': service_file.name,
            'service_name': service_name,
            'controller_log_file': controller_log_file,
            'remote_user_config_path': remote_config_yaml_path,
            'modified_catalogs':
                service_catalog_common.get_modified_catalog_file_mounts(),
            **controller_utils.shared_controller_vars_to_fill(
                controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
                remote_user_config_path=remote_config_yaml_path,
                local_user_config=mutated_user_config,
            ),
        }
        common_utils.fill_template(serve_constants.CONTROLLER_TEMPLATE,
                                   vars_to_fill,
                                   output_path=controller_file.name)
        controller_task = task_lib.Task.from_yaml(controller_file.name)
        # TODO(tian): Probably run another sky.launch after we get the load
        # balancer port from the controller? So we don't need to open so many
        # ports here. Or, we should have a nginx traffic control to refuse
        # any connection to the unregistered ports.
        controller_resources = {
            r.copy(ports=[serve_constants.LOAD_BALANCER_PORT_RANGE])
            for r in controller_resources
        }
        controller_task.set_resources(controller_resources)

        # # Set service_name so the backend will know to modify default ray
        # task CPU usage to custom value instead of default 0.5 vCPU. We need
        # to set it to a smaller value to support a larger number of services.
        controller_task.service_name = service_name

        print(f'{colorama.Fore.YELLOW}Launching controller for '
              f'{service_name!r}...{colorama.Style.RESET_ALL}')
        # We directly submit the request to the controller and let the
        # controller to check name conflict. Suppose we have multiple
        # sky.serve.up() with same service name, the first one will
        # successfully write its job id to controller service database;
        # and for all following sky.serve.up(), the controller will throw
        # an exception (name conflict detected) and exit. Therefore the
        # controller job id in database could be use as an indicator of
        # whether the service is already running. If the id is the same
        # with the current job id, we know the service is up and running
        # for the first time; otherwise it is a name conflict.
        idle_minutes_to_autostop = constants.CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP
        controller_job_id, controller_handle = sky.launch(
            task=controller_task,
            stream_logs=False,
            cluster_name=controller_name,
            detach_run=True,
            idle_minutes_to_autostop=idle_minutes_to_autostop,
            retry_until_up=True,
            _disable_controller_check=True,
        )

        style = colorama.Style
        fore = colorama.Fore

        assert controller_job_id is not None and controller_handle is not None
        # TODO(tian): Cache endpoint locally to speedup. Endpoint won't
        # change after the first time, so there is no consistency issue.
        with rich_utils.safe_status(
                ux_utils.spinner_message(
                    'Waiting for the service to register')):
            # This function will check the controller job id in the database
            # and return the endpoint if the job id matches. Otherwise it will
            # return None.
            code = serve_utils.ServeCodeGen.wait_service_registration(
                service_name, controller_job_id)
            backend = backend_utils.get_backend_from_handle(controller_handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            assert isinstance(controller_handle,
                              backends.CloudVmRayResourceHandle)
            returncode, lb_port_payload, _ = backend.run_on_head(
                controller_handle,
                code,
                require_outputs=True,
                stream_logs=False)
        try:
            subprocess_utils.handle_returncode(
                returncode, code, 'Failed to wait for service initialization',
                lb_port_payload)
        except exceptions.CommandError:
            statuses = backend.get_job_status(controller_handle,
                                              [controller_job_id],
                                              stream_logs=False)
            controller_job_status = list(statuses.values())[0]
            if controller_job_status == sky.JobStatus.PENDING:
                # Max number of services reached due to vCPU constraint.
                # The controller job is pending due to ray job scheduling.
                # We manually cancel the job here.
                backend.cancel_jobs(controller_handle, [controller_job_id])
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'Max number of services reached. '
                        'To spin up more services, please '
                        'tear down some existing services.') from None
            else:
                # Possible cases:
                # (1) name conflict;
                # (2) max number of services reached due to memory
                # constraint. The job will successfully run on the
                # controller, but there will be an error thrown due
                # to memory constraint check in the controller.
                # See sky/serve/service.py for more details.
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'Failed to spin up the service. Please '
                        'check the logs above for more details.') from None
        else:
            lb_port = serve_utils.load_service_initialization_result(
                lb_port_payload)
            endpoint = backend_utils.get_endpoints(
                controller_handle.cluster_name, lb_port,
                skip_status_check=True).get(lb_port)
            assert endpoint is not None, 'Did not get endpoint for controller.'

        sky_logging.print(
            f'{fore.CYAN}Service name: '
            f'{style.BRIGHT}{service_name}{style.RESET_ALL}'
            f'\n{fore.CYAN}Endpoint URL: '
            f'{style.BRIGHT}{endpoint}{style.RESET_ALL}'
            f'\n📋 Useful Commands'
            f'\n{ux_utils.INDENT_SYMBOL}To check service status:\t'
            f'{ux_utils.BOLD}sky serve status {service_name} '
            f'[--endpoint]{ux_utils.RESET_BOLD}'
            f'\n{ux_utils.INDENT_SYMBOL}To teardown the service:\t'
            f'{ux_utils.BOLD}sky serve down {service_name}'
            f'{ux_utils.RESET_BOLD}'
            f'\n{ux_utils.INDENT_SYMBOL}To see replica logs:\t'
            f'{ux_utils.BOLD}sky serve logs {service_name} [REPLICA_ID]'
            f'{ux_utils.RESET_BOLD}'
            f'\n{ux_utils.INDENT_SYMBOL}To see load balancer logs:\t'
            f'{ux_utils.BOLD}sky serve logs --load-balancer {service_name}'
            f'{ux_utils.RESET_BOLD}'
            f'\n{ux_utils.INDENT_SYMBOL}To see controller logs:\t'
            f'{ux_utils.BOLD}sky serve logs --controller {service_name}'
            f'{ux_utils.RESET_BOLD}'
            f'\n{ux_utils.INDENT_SYMBOL}To monitor the status:\t'
            f'{ux_utils.BOLD}watch -n10 sky serve status {service_name}'
            f'{ux_utils.RESET_BOLD}'
            f'\n{ux_utils.INDENT_LAST_SYMBOL}To send a test request:\t'
            f'{ux_utils.BOLD}curl {endpoint}'
            f'{ux_utils.RESET_BOLD}'
            '\n\n' +
            ux_utils.finishing_message('Service is spinning up and replicas '
                                       'will be ready shortly.'))
        return service_name, endpoint


@usage_lib.entrypoint
def update(
        task: 'sky.Task',
        service_name: str,
        mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE) -> None:
    """Update an existing service.

    Please refer to the sky.cli.serve_update for the document.

    Args:
        task: sky.Task to update.
        service_name: Name of the service.
    """
    _validate_service_task(task)
    handle = backend_utils.is_controller_accessible(
        controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message=
        'Service controller is stopped. There is no service to update. '
        f'To spin up a new service, use {ux_utils.BOLD}'
        f'sky serve up{ux_utils.RESET_BOLD}',
        non_existent_message='Service does not exist. '
        'To spin up a new service, '
        f'use {ux_utils.BOLD}sky serve up{ux_utils.RESET_BOLD}',
    )

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = serve_utils.ServeCodeGen.get_service_status([service_name])
    returncode, serve_status_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)
    try:
        subprocess_utils.handle_returncode(returncode,
                                           code, 'Failed to get service status '
                                           'when update service',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    service_statuses = serve_utils.load_service_status(serve_status_payload)
    if len(service_statuses) == 0:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Cannot find service {service_name!r}.'
                               f'To spin up a service, use {ux_utils.BOLD}'
                               f'sky serve up{ux_utils.RESET_BOLD}')

    if len(service_statuses) > 1:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Multiple services found for {service_name!r}. ')
    service_record = service_statuses[0]
    prompt = None
    if (service_record['status'] == serve_state.ServiceStatus.CONTROLLER_FAILED
       ):
        prompt = (f'Service {service_name!r} has a failed controller. '
                  'Please clean up the service and try again.')
    elif (service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT
         ):
        prompt = (f'Service {service_name!r} is still initializing '
                  'its controller. Please try again later.')
    if prompt is not None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(prompt)

    with rich_utils.safe_status(
            ux_utils.spinner_message('Initializing service')):
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task, path='serve')

    code = serve_utils.ServeCodeGen.add_version(service_name)
    returncode, version_string_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)
    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           'Failed to add version',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    version_string = serve_utils.load_version_string(version_string_payload)
    try:
        current_version = int(version_string)
    except ValueError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Failed to parse version: {version_string}; '
                             f'Returncode: {returncode}') from e

    print(f'New version: {current_version}')
    with tempfile.NamedTemporaryFile(
            prefix=f'{service_name}-v{current_version}',
            mode='w') as service_file:
        task_config = task.to_yaml_config()
        common_utils.dump_yaml(service_file.name, task_config)
        remote_task_yaml_path = serve_utils.generate_task_yaml_file_name(
            service_name, current_version, expand_user=False)

        backend.sync_file_mounts(handle,
                                 {remote_task_yaml_path: service_file.name},
                                 storage_mounts=None)

        code = serve_utils.ServeCodeGen.update_service(service_name,
                                                       current_version,
                                                       mode=mode.value)
        returncode, _, stderr = backend.run_on_head(handle,
                                                    code,
                                                    require_outputs=True,
                                                    stream_logs=False,
                                                    separate_stderr=True)
        try:
            subprocess_utils.handle_returncode(returncode,
                                               code,
                                               'Failed to update services',
                                               stderr,
                                               stream_logs=True)
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

    print(f'{colorama.Fore.GREEN}Service {service_name!r} update scheduled.'
          f'{colorama.Style.RESET_ALL}\n'
          f'Please use {ux_utils.BOLD}sky serve status {service_name} '
          f'{ux_utils.RESET_BOLD}to check the latest status.')


@usage_lib.entrypoint
# pylint: disable=redefined-builtin
def down(
    service_names: Optional[Union[str, List[str]]] = None,
    all: bool = False,
    purge: bool = False,
) -> None:
    """Teardown a service.

    Please refer to the sky.cli.serve_down for the docs.

    Args:
        service_names: Name of the service(s).
        all: Whether to terminate all services.
        purge: Whether to terminate services in a failed status. These services
          may potentially lead to resource leaks.

    Raises:
        sky.exceptions.ClusterNotUpError: if the sky serve controller is not up.
        ValueError: if the arguments are invalid.
        RuntimeError: if failed to terminate the service.
    """
    if service_names is None:
        service_names = []
    if isinstance(service_names, str):
        service_names = [service_names]
    handle = backend_utils.is_controller_accessible(
        controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message='All services should have terminated.')

    service_names_str = ','.join(service_names)
    if sum([len(service_names) > 0, all]) != 1:
        argument_str = f'service_names={service_names_str}' if len(
            service_names) > 0 else ''
        argument_str += ' all' if all else ''
        raise ValueError('Can only specify one of service_names or all. '
                         f'Provided {argument_str!r}.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    service_names = None if all else service_names
    code = serve_utils.ServeCodeGen.terminate_services(service_names, purge)

    try:
        returncode, stdout, _ = backend.run_on_head(handle,
                                                    code,
                                                    require_outputs=True,
                                                    stream_logs=False)
    except exceptions.FetchClusterInfoError as e:
        raise RuntimeError(
            'Failed to fetch controller IP. Please refresh controller status '
            f'by `sky status -r {serve_utils.SKY_SERVE_CONTROLLER_NAME}` '
            'and try again.') from e

    try:
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to terminate service',
                                           stdout)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    sky_logging.print(stdout)


@usage_lib.entrypoint
def terminate_replica(service_name: str, replica_id: int, purge: bool) -> None:
    """Tear down a specific replica for the given service.

    Args:
        service_name: Name of the service.
        replica_id: ID of replica to terminate.
        purge: Whether to terminate replicas in a failed status. These replicas
          may lead to resource leaks, so we require the user to explicitly
          specify this flag to make sure they are aware of this potential
          resource leak.

    Raises:
        sky.exceptions.ClusterNotUpError: if the sky sere controller is not up.
        RuntimeError: if failed to terminate the replica.
    """
    handle = backend_utils.is_controller_accessible(
        controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message=
        'No service is running now. Please spin up a service first.',
        non_existent_message='No service is running now. '
        'Please spin up a service first.',
    )

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = serve_utils.ServeCodeGen.terminate_replica(service_name, replica_id,
                                                      purge)
    returncode, stdout, stderr = backend.run_on_head(handle,
                                                     code,
                                                     require_outputs=True,
                                                     stream_logs=False,
                                                     separate_stderr=True)

    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           'Failed to terminate the replica',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    sky_logging.print(stdout)


@usage_lib.entrypoint
def status(
    service_names: Optional[Union[str,
                                  List[str]]] = None) -> List[Dict[str, Any]]:
    """Get service statuses.

    If service_names is given, return those services. Otherwise, return all
    services.

    Each returned value has the following fields:

    .. code-block:: python

        {
            'name': (str) service name,
            'active_versions': (List[int]) a list of versions that are active,
            'controller_job_id': (int) the job id of the controller,
            'uptime': (int) uptime in seconds,
            'status': (sky.ServiceStatus) service status,
            'controller_port': (Optional[int]) controller port,
            'load_balancer_port': (Optional[int]) load balancer port,
            'policy': (Optional[str]) load balancer policy description,
            'requested_resources': (sky.Resources) requested resources
              for replica (deprecated),
            'requested_resources_str': (str) str representation of
              requested resources,
            'replica_info': (List[Dict[str, Any]]) replica information,
        }

    Each entry in replica_info has the following fields:

    .. code-block:: python

        {
            'replica_id': (int) replica id,
            'name': (str) replica name,
            'status': (sky.serve.ReplicaStatus) replica status,
            'version': (int) replica version,
            'launched_at': (int) timestamp of launched,
            'handle': (ResourceHandle) handle of the replica cluster,
        }

    For possible service statuses and replica statuses, please refer to
    sky.cli.serve_status.

    Args:
        service_names: a single or a list of service names to query. If None,
            query all services.

    Returns:
        A list of dicts, with each dict containing the information of a service.
        If a service is not found, it will be omitted from the returned list.

    Raises:
        RuntimeError: if failed to get the service status.
        exceptions.ClusterNotUpError: if the sky serve controller is not up.
    """
    if service_names is not None:
        if isinstance(service_names, str):
            service_names = [service_names]

    try:
        backend_utils.check_network_connection()
    except exceptions.NetworkError as e:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Failed to refresh service status due to network error.') from e

    controller_type = controller_utils.Controllers.SKY_SERVE_CONTROLLER
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=controller_type.value.default_hint_if_non_existent)

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = serve_utils.ServeCodeGen.get_service_status(service_names)
    returncode, serve_status_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)

    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           'Failed to fetch services',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    return serve_utils.load_service_status(serve_status_payload)


@usage_lib.entrypoint
def tail_logs(
    service_name: str,
    *,
    target: Union[str, serve_utils.ServiceComponent],
    replica_id: Optional[int] = None,
    follow: bool = True,
) -> None:
    """Tail logs for a service.

    Usage:
        sky.serve.tail_logs(
            service_name,
            target=<component>,
            follow=False, # Optionally, default to True
            # replica_id=3, # Must be specified when target is REPLICA.
        )

    `target` is a enum of sky.serve.ServiceComponent, which can be one of:
        - CONTROLLER
        - LOAD_BALANCER
        - REPLICA
    Pass target as a lower-case string is also supported, e.g.
    target='controller'.
    To use REPLICA, you must specify `replica_id`.

    To tail controller logs:
        # follow default to True
        sky.serve.tail_logs(
            service_name, target=sky.serve.ServiceComponent.CONTROLLER)

    To print replica 3 logs:
        # Pass target as a lower-case string is also supported.
        sky.serve.tail_logs(
            service_name, target='replica',
            follow=False, replica_id=3)

    Raises:
        sky.exceptions.ClusterNotUpError: the sky serve controller is not up.
        ValueError: arguments not valid, or failed to tail the logs.
    """
    if isinstance(target, str):
        target = serve_utils.ServiceComponent(target)
    if not isinstance(target, serve_utils.ServiceComponent):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'`target` must be a string or '
                             f'sky.serve.ServiceComponent, got {type(target)}.')
    if target == serve_utils.ServiceComponent.REPLICA:
        if replica_id is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    '`replica_id` must be specified when using target=REPLICA.')
    else:
        if replica_id is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('`replica_id` must be None when using '
                                 'target=CONTROLLER/LOAD_BALANCER.')
    handle = backend_utils.is_controller_accessible(
        controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message=(controller_utils.Controllers.SKY_SERVE_CONTROLLER.
                         value.default_hint_if_non_existent))

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend
    backend.tail_serve_logs(handle,
                            service_name,
                            target,
                            replica_id,
                            follow=follow)

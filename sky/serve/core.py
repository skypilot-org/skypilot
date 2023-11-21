"""SkyServe core APIs."""
import re
import tempfile
from typing import Any, Dict, List, Optional, Union

import colorama

import sky
from sky import backends
from sky import exceptions
from sky import execution
from sky import global_user_state
from sky import sky_logging
from sky import status_lib
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds import gcp
from sky.serve import constants as serve_constants
from sky.serve import serve_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils


@usage_lib.entrypoint
def up(
    task: 'sky.Task',
    service_name: Optional[str] = None,
) -> None:
    """Spin up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.
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

    if task.service is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Service section not found.')

    assert len(task.resources) == 1, task
    requested_resources = list(task.resources)[0]
    if requested_resources.ports is None or len(requested_resources.ports) != 1:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Must only specify one port in resources. Each replica '
                'will use the port specified as application ingress port.')

    controller_utils.maybe_translate_local_file_mounts_and_sync_up(task,
                                                                   path='serve')

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
        extra_vars, controller_resources_config = (
            controller_utils.skypilot_config_setup(
                controller_type='serve',
                controller_resources_config=serve_constants.
                CONTROLLER_RESOURCES,
                remote_user_config_path=remote_config_yaml_path))
        try:
            controller_resources = sky.Resources.from_yaml_config(
                controller_resources_config)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    controller_utils.CONTROLLER_RESOURCES_NOT_VALID_MESSAGE.
                    format(controller_type='serve',
                           err=common_utils.format_exception(
                               e, use_bracket=True))) from e
        vars_to_fill = {
            'remote_task_yaml_path': remote_tmp_task_yaml_path,
            'local_task_yaml_path': service_file.name,
            'google_sdk_installation_commands':
                gcp.GOOGLE_SDK_INSTALLATION_COMMAND,
            'service_name': service_name,
            'controller_log_file': controller_log_file,
            **extra_vars,
        }
        backend_utils.fill_template(serve_constants.CONTROLLER_TEMPLATE,
                                    vars_to_fill,
                                    output_path=controller_file.name)
        controller_task = task_lib.Task.from_yaml(controller_file.name)
        controller_exist = (
            global_user_state.get_cluster_from_name(controller_name)
            is not None)
        controller_cloud = (
            requested_resources.cloud if not controller_exist and
            controller_resources.cloud is None else controller_resources.cloud)
        # TODO(tian): Probably run another sky.launch after we get the load
        # balancer port from the controller? So we don't need to open so many
        # ports here. Or, we should have a nginx traffic control to refuse
        # any connection to the unregistered ports.
        controller_resources = controller_resources.copy(
            cloud=controller_cloud,
            ports=[serve_constants.LOAD_BALANCER_PORT_RANGE])
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
        controller_job_id, controller_handle = execution.execute(
            entrypoint=controller_task,
            stream_logs=False,
            cluster_name=controller_name,
            detach_run=True,
            idle_minutes_to_autostop=constants.
            CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
            retry_until_up=True,
        )

        style = colorama.Style
        fore = colorama.Fore

        assert controller_job_id is not None and controller_handle is not None
        # TODO(tian): Cache endpoint locally to speedup. Endpoint won't
        # change after the first time, so there is no consistency issue.
        with rich_utils.safe_status(
                '[cyan]Waiting for the service to initialize[/]'):
            # This function will check the controller job id in the database
            # and return the endpoint if the job id matches. Otherwise it will
            # return None.
            code = serve_utils.ServeCodeGen.wait_service_initialization(
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
            endpoint = f'{controller_handle.head_ip}:{lb_port}'

        sky_logging.print(
            f'{fore.CYAN}Service name: '
            f'{style.BRIGHT}{service_name}{style.RESET_ALL}'
            f'\n{fore.CYAN}Endpoint URL: '
            f'{style.BRIGHT}{endpoint}{style.RESET_ALL}'
            '\nTo see detailed info:\t\t'
            f'{backend_utils.BOLD}sky serve status {service_name} '
            f'[--endpoint]{backend_utils.RESET_BOLD}'
            '\nTo teardown the service:\t'
            f'{backend_utils.BOLD}sky serve down {service_name}'
            f'{backend_utils.RESET_BOLD}'
            '\n'
            '\nTo see logs of a replica:\t'
            f'{backend_utils.BOLD}sky serve logs {service_name} [REPLICA_ID]'
            f'{backend_utils.RESET_BOLD}'
            '\nTo see logs of load balancer:\t'
            f'{backend_utils.BOLD}sky serve logs --load-balancer {service_name}'
            f'{backend_utils.RESET_BOLD}'
            '\nTo see logs of controller:\t'
            f'{backend_utils.BOLD}sky serve logs --controller {service_name}'
            f'{backend_utils.RESET_BOLD}'
            '\n'
            '\nTo monitor replica status:\t'
            f'{backend_utils.BOLD}watch -n10 sky serve status {service_name}'
            f'{backend_utils.RESET_BOLD}'
            '\nTo send a test request:\t\t'
            f'{backend_utils.BOLD}curl -L {endpoint}'
            f'{backend_utils.RESET_BOLD}'
            '\n'
            f'\n{fore.GREEN}SkyServe is spinning up your service now.'
            f'{style.RESET_ALL}'
            f'\n{fore.GREEN}The replicas should be ready within a '
            f'short time.{style.RESET_ALL}')


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
    cluster_status, handle = backend_utils.is_controller_up(
        controller_type=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message='All services should have terminated.')
    if handle is None or handle.head_ip is None:
        # The error message is already printed in
        # backend_utils.is_controller_up
        # TODO(zhwu): Move the error message into the exception.
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(message='',
                                               cluster_status=cluster_status)

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
    except exceptions.FetchIPError as e:
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
            'controller_job_id': (int) the job id of the controller,
            'uptime': (int) uptime in seconds,
            'status': (sky.ServiceStatus) service status,
            'controller_port': (Optional[int]) controller port,
            'load_balancer_port': (Optional[int]) load balancer port,
            'policy': (Optional[str]) load balancer policy description,
            'auto_restart': (bool) whether the service replica will be
              auto-restarted,
            'requested_resources': (sky.Resources) requested resources
              for replica,
            'replica_info': (List[Dict[str, Any]]) replica information,
        }

    Each entry in replica_info has the following fields:

    .. code-block:: python

        {
            'replica_id': (int) replica id,
            'name': (str) replica name,
            'status': (sky.serve.ReplicaStatus) replica status,
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

    # TODO(tian): This is so slow... It will take ~10s to refresh the status
    # of controller. Can we optimize this?
    controller_status, handle = backend_utils.is_controller_up(
        controller_type=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message='No service is found.')

    if handle is None or handle.head_ip is None:
        # When the controller is STOPPED, the head_ip will be None, as
        # it will be set in global_user_state.remove_cluster().
        # We do not directly check for UP because the controller may be
        # in INIT state during another `sky serve up`, but still have
        # head_ip available. In this case, we can still try to ssh
        # into the controller and fetch the job table.
        raise exceptions.ClusterNotUpError('Sky serve controller is not up.',
                                           cluster_status=controller_status)

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
    controller_status, handle = backend_utils.is_controller_up(
        controller_type=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
        stopped_message='No service is found.')
    if handle is None or handle.head_ip is None:
        msg = 'No service is found.'
        if controller_status == status_lib.ClusterStatus.INIT:
            msg = ''
        raise exceptions.ClusterNotUpError(msg,
                                           cluster_status=controller_status)
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend
    backend.tail_serve_logs(handle,
                            service_name,
                            target,
                            replica_id,
                            follow=follow)

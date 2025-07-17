"""SkyServe core APIs."""
import pathlib
import re
import signal
import tempfile
import threading
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import colorama

import sky
from sky import backends
from sky import exceptions
from sky import execution
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.backends import backend_utils
from sky.catalog import common as service_catalog_common
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.usage import usage_lib
from sky.utils import admin_policy_utils
from sky.utils import command_runner
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)


def _rewrite_tls_credential_paths_and_get_tls_env_vars(
        service_name: str, task: 'sky.Task') -> Dict[str, Any]:
    """Rewrite the paths of TLS credentials in the task.

    Args:
        service_name: Name of the service.
        task: sky.Task to rewrite.

    Returns:
        The generated template variables for TLS.
    """
    service_spec = task.service
    # Already checked by validate_service_task
    assert service_spec is not None
    if service_spec.tls_credential is None:
        return {'use_tls': False}
    remote_tls_keyfile = (
        serve_utils.generate_remote_tls_keyfile_name(service_name))
    remote_tls_certfile = (
        serve_utils.generate_remote_tls_certfile_name(service_name))
    tls_template_vars = {
        'use_tls': True,
        'remote_tls_keyfile': remote_tls_keyfile,
        'remote_tls_certfile': remote_tls_certfile,
        'local_tls_keyfile': service_spec.tls_credential.keyfile,
        'local_tls_certfile': service_spec.tls_credential.certfile,
    }
    service_spec.tls_credential = serve_utils.TLSCredential(
        remote_tls_keyfile, remote_tls_certfile)
    return tls_template_vars


def _get_all_replica_targets(
    service_name: str, backend: backends.CloudVmRayBackend,
    handle: backends.CloudVmRayResourceHandle
) -> Set[serve_utils.ServiceComponentTarget]:
    """Helper function to get targets for all live replicas."""
    code = serve_utils.ServeCodeGen.get_service_status([service_name])
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

    service_records = serve_utils.load_service_status(serve_status_payload)
    if not service_records:
        raise ValueError(f'Service {service_name!r} not found.')
    assert len(service_records) == 1
    service_record = service_records[0]

    return {
        serve_utils.ServiceComponentTarget(serve_utils.ServiceComponent.REPLICA,
                                           replica_info['replica_id'])
        for replica_info in service_record['replica_info']
    }


@usage_lib.entrypoint
def up(
    task: 'sky.Task',
    service_name: Optional[str] = None,
) -> Tuple[str, str]:
    """Spins up a service.

    Please refer to the sky.cli.serve_up for the document.

    Args:
        task: sky.Task to serve up.
        service_name: Name of the service.

    Returns:
        service_name: str; The name of the service.  Same if passed in as an
            argument.
        endpoint: str; The service endpoint.
    """
    task.validate()
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

    serve_utils.validate_service_task(task)
    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag.resolve_and_validate_volumes()
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    dag, mutated_user_config = admin_policy_utils.apply(dag)
    dag.pre_mount_volumes()
    task = dag.tasks[0]

    with rich_utils.safe_status(
            ux_utils.spinner_message('Initializing service')):
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task, task_type='serve')

    tls_template_vars = _rewrite_tls_credential_paths_and_get_tls_env_vars(
        service_name, task)

    with tempfile.NamedTemporaryFile(
            prefix=f'service-task-{service_name}-',
            mode='w',
    ) as service_file, tempfile.NamedTemporaryFile(
            prefix=f'controller-task-{service_name}-',
            mode='w',
    ) as controller_file:
        controller_name = common.SKY_SERVE_CONTROLLER_NAME
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
            **tls_template_vars,
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
        # Since the controller may be shared among multiple users, launch the
        # controller with the API server's user hash.
        with common.with_server_user():
            with skypilot_config.local_active_workspace_ctx(
                    constants.SKYPILOT_DEFAULT_WORKSPACE):
                controller_job_id, controller_handle = execution.launch(
                    task=controller_task,
                    cluster_name=controller_name,
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
            socket_endpoint = backend_utils.get_endpoints(
                controller_handle.cluster_name, lb_port,
                skip_status_check=True).get(lb_port)
            assert socket_endpoint is not None, (
                'Did not get endpoint for controller.')
            # Already checked by validate_service_task
            assert task.service is not None
            protocol = ('http'
                        if task.service.tls_credential is None else 'https')
            socket_endpoint = socket_endpoint.replace('https://', '').replace(
                'http://', '')
            endpoint = f'{protocol}://{socket_endpoint}'

        logger.info(
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
    """Updates an existing service.

    Please refer to the sky.cli.serve_update for the document.

    Args:
        task: sky.Task to update.
        service_name: Name of the service.
        mode: Update mode.
    """
    task.validate()
    serve_utils.validate_service_task(task)

    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    # TODO(cblmemo,zhwu): If a user sets a new skypilot_config, the update
    # will not apply the config.
    dag, _ = admin_policy_utils.apply(task)
    task = dag.tasks[0]

    assert task.service is not None
    if task.service.tls_credential is not None:
        logger.warning('Updating TLS keyfile and certfile is not supported. '
                       'Any updates to the keyfile and certfile will not take '
                       'effect. To update TLS keyfile and certfile, please '
                       'tear down the service and spin up a new one.')

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
    if not service_statuses:
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

    original_lb_policy = service_record['load_balancing_policy']
    assert task.service is not None, 'Service section not found.'
    if original_lb_policy != task.service.load_balancing_policy:
        logger.warning(
            f'{colorama.Fore.YELLOW}Current load balancing policy '
            f'{original_lb_policy!r} is different from the new policy '
            f'{task.service.load_balancing_policy!r}. Updating the load '
            'balancing policy is not supported yet and it will be ignored. '
            'The service will continue to use the current load balancing '
            f'policy.{colorama.Style.RESET_ALL}')

    with rich_utils.safe_status(
            ux_utils.spinner_message('Initializing service')):
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task, task_type='serve')

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
    """Tears down a service.

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
    if sum([bool(service_names), all]) != 1:
        argument_str = (f'service_names={service_names_str}'
                        if service_names else '')
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
            f'by `sky status -r {common.SKY_SERVE_CONTROLLER_NAME}` '
            'and try again.') from e

    try:
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to terminate service',
                                           stdout)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    logger.info(stdout)


@usage_lib.entrypoint
def terminate_replica(service_name: str, replica_id: int, purge: bool) -> None:
    """Tears down a specific replica for the given service.

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
    """Gets service statuses.

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
            'endpoint': (Optional[str]) load balancer endpoint,
            'policy': (Optional[str]) autoscaling policy description,
            'requested_resources_str': (str) str representation of
              requested resources,
            'load_balancing_policy': (str) load balancing policy name,
            'tls_encrypted': (bool) whether the service is TLS encrypted,
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
            'endpoint': (str) endpoint of the replica,
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

    service_records = serve_utils.load_service_status(serve_status_payload)
    # Get the endpoint for each service
    for service_record in service_records:
        service_record['endpoint'] = None
        if service_record['load_balancer_port'] is not None:
            try:
                endpoint = backend_utils.get_endpoints(
                    cluster=common.SKY_SERVE_CONTROLLER_NAME,
                    port=service_record['load_balancer_port']).get(
                        service_record['load_balancer_port'], None)
            except exceptions.ClusterNotUpError:
                pass
            else:
                protocol = ('https'
                            if service_record['tls_encrypted'] else 'http')
                if endpoint is not None:
                    endpoint = endpoint.replace('https://',
                                                '').replace('http://', '')
                service_record['endpoint'] = f'{protocol}://{endpoint}'

    return service_records


ServiceComponentOrStr = Union[str, serve_utils.ServiceComponent]


@usage_lib.entrypoint
def tail_logs(
    service_name: str,
    *,
    target: ServiceComponentOrStr,
    replica_id: Optional[int] = None,
    follow: bool = True,
) -> None:
    """Tails logs for a service.

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

    controller_type = controller_utils.Controllers.SKY_SERVE_CONTROLLER
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=controller_type.value.default_hint_if_non_existent)

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend

    if target != serve_utils.ServiceComponent.REPLICA:
        code = serve_utils.ServeCodeGen.stream_serve_process_logs(
            service_name,
            stream_controller=(
                target == serve_utils.ServiceComponent.CONTROLLER),
            follow=follow)
    else:
        assert replica_id is not None, service_name
        code = serve_utils.ServeCodeGen.stream_replica_logs(
            service_name, replica_id, follow)

    # With the stdin=subprocess.DEVNULL, the ctrl-c will not directly
    # kill the process, so we need to handle it manually here.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, backend_utils.interrupt_handler)
        signal.signal(signal.SIGTSTP, backend_utils.stop_handler)

    # Refer to the notes in
    # sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend::tail_logs.
    backend.run_on_head(handle,
                        code,
                        stream_logs=True,
                        process_stream=False,
                        ssh_mode=command_runner.SshMode.INTERACTIVE)


@usage_lib.entrypoint
def sync_down_logs(
    service_name: str,
    *,
    local_dir: str,
    targets: Union[ServiceComponentOrStr, List[ServiceComponentOrStr],
                   None] = None,
    replica_ids: Optional[List[int]] = None,
) -> str:
    """Sync down logs from the controller for the given service.

    This function is called by the server endpoint. It gathers logs from the
    controller, load balancer, and/or replicas and places them in a directory
    under the user's log space on the API server filesystem.

    Args:
        service_name: The name of the service to download logs from.
        local_dir: The local directory to save the logs to.
        targets: Which component(s) to download logs for. If None or empty,
            means download all logs (controller, load-balancer, all replicas).
            Can be a string (e.g. "controller"), or a `ServiceComponent` object,
            or a list of them for multiple components. Currently accepted
            values:
                - "controller"/ServiceComponent.CONTROLLER
                - "load_balancer"/ServiceComponent.LOAD_BALANCER
                - "replica"/ServiceComponent.REPLICA
        replica_ids: The list of replica IDs to download logs from, specified
            when target includes `ServiceComponent.REPLICA`. If target includes
            `ServiceComponent.REPLICA` but this is None/empty, logs for all
            replicas will be downloaded.

    Returns:
        A dict mapping component names to local paths where the logs were synced
        down to.

    Raises:
        RuntimeError: If fails to gather logs or fails to rsync from the
          controller.
        sky.exceptions.ClusterNotUpError: If the controller is not up.
        ValueError: Arguments not valid.
    """
    # Step 0) get the controller handle
    with rich_utils.safe_status(
            ux_utils.spinner_message('Checking service status...')):
        controller_type = controller_utils.Controllers.SKY_SERVE_CONTROLLER
        handle = backend_utils.is_controller_accessible(
            controller=controller_type,
            stopped_message=controller_type.value.default_hint_if_non_existent)
        backend: backends.CloudVmRayBackend = (
            backend_utils.get_backend_from_handle(handle))

    requested_components: Set[serve_utils.ServiceComponent] = set()
    if not targets:
        # No targets specified -> request all components
        requested_components = {
            serve_utils.ServiceComponent.CONTROLLER,
            serve_utils.ServiceComponent.LOAD_BALANCER,
            serve_utils.ServiceComponent.REPLICA
        }
    else:
        # Parse provided targets
        if isinstance(targets, (str, serve_utils.ServiceComponent)):
            requested_components = {serve_utils.ServiceComponent(targets)}
        else:  # list
            requested_components = {
                serve_utils.ServiceComponent(t) for t in targets
            }

    normalized_targets: Set[serve_utils.ServiceComponentTarget] = set()
    if serve_utils.ServiceComponent.CONTROLLER in requested_components:
        normalized_targets.add(
            serve_utils.ServiceComponentTarget(
                serve_utils.ServiceComponent.CONTROLLER))
    if serve_utils.ServiceComponent.LOAD_BALANCER in requested_components:
        normalized_targets.add(
            serve_utils.ServiceComponentTarget(
                serve_utils.ServiceComponent.LOAD_BALANCER))
    if serve_utils.ServiceComponent.REPLICA in requested_components:
        with rich_utils.safe_status(
                ux_utils.spinner_message('Getting live replica infos...')):
            replica_targets = _get_all_replica_targets(service_name, backend,
                                                       handle)
        if not replica_ids:
            # Replica target requested but no specific IDs
            # -> Get all replica logs
            normalized_targets.update(replica_targets)
        else:
            # Replica target requested with specific IDs
            requested_replica_targets = [
                serve_utils.ServiceComponentTarget(
                    serve_utils.ServiceComponent.REPLICA, rid)
                for rid in replica_ids
            ]
            for target in requested_replica_targets:
                if target not in replica_targets:
                    logger.warning(f'Replica ID {target.replica_id} not found '
                                   f'for {service_name}. Skipping...')
                else:
                    normalized_targets.add(target)

    def sync_down_logs_by_target(target: serve_utils.ServiceComponentTarget):
        component = target.component
        # We need to set one side of the pipe to a logs stream, and the other
        # side to a file.
        log_path = str(pathlib.Path(local_dir) / f'{target}.log')
        stream_logs_code: str

        if component == serve_utils.ServiceComponent.CONTROLLER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name, stream_controller=True, follow=False))
        elif component == serve_utils.ServiceComponent.LOAD_BALANCER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name, stream_controller=False, follow=False))
        elif component == serve_utils.ServiceComponent.REPLICA:
            replica_id = target.replica_id
            assert replica_id is not None, service_name
            stream_logs_code = serve_utils.ServeCodeGen.stream_replica_logs(
                service_name, replica_id, follow=False)
        else:
            assert False, component

        # Refer to the notes in
        # sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend::tail_logs.
        backend.run_on_head(handle,
                            stream_logs_code,
                            stream_logs=False,
                            process_stream=False,
                            ssh_mode=command_runner.SshMode.INTERACTIVE,
                            log_path=log_path)

    subprocess_utils.run_in_parallel(sync_down_logs_by_target,
                                     list(normalized_targets))

    return local_dir

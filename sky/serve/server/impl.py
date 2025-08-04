"""Implementation of the SkyServe core APIs."""
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

import colorama
import filelock

import sky
from sky import backends
from sky import exceptions
from sky import execution
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.backends import backend_utils
from sky.catalog import common as service_catalog_common
from sky.data import storage as storage_lib
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.utils import admin_policy_utils
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

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


def _get_service_record(
        service_name: str, pool: bool,
        handle: backends.CloudVmRayResourceHandle,
        backend: backends.CloudVmRayBackend) -> Optional[Dict[str, Any]]:
    """Get the service record."""
    noun = 'pool' if pool else 'service'

    code = serve_utils.ServeCodeGen.get_service_status([service_name],
                                                       pool=pool)
    returncode, serve_status_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)
    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           f'Failed to get {noun} status',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    service_statuses = serve_utils.load_service_status(serve_status_payload)

    assert len(service_statuses) <= 1, service_statuses
    if not service_statuses:
        return None
    return service_statuses[0]


def up(
    task: 'sky.Task',
    service_name: Optional[str] = None,
    pool: bool = False,
) -> Tuple[str, str]:
    """Spins up a service or a pool."""
    if pool and not serve_utils.is_consolidation_mode(pool):
        raise ValueError(
            'Pool is only supported in consolidation mode. To fix, set '
            '`jobs.controller.consolidation_mode: true` in SkyPilot config.')
    task.validate()
    serve_utils.validate_service_task(task, pool=pool)
    assert task.service is not None
    assert task.service.pool == pool, 'Inconsistent pool flag.'
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()
    if service_name is None:
        service_name = serve_utils.generate_service_name(pool)

    # The service name will be used as:
    # 1. controller cluster name: 'sky-serve-controller-<service_name>'
    # 2. replica cluster name: '<service_name>-<replica_id>'
    # In both cases, service name shares the same regex with cluster name.
    if re.fullmatch(constants.CLUSTER_NAME_VALID_REGEX, service_name) is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'{capnoun} name {service_name!r} is invalid: '
                             f'ensure it is fully matched by regex (e.g., '
                             'only contains lower letters, numbers and dash): '
                             f'{constants.CLUSTER_NAME_VALID_REGEX}')

    dag = dag_utils.convert_entrypoint_to_dag(task)
    dag.resolve_and_validate_volumes()
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    dag, mutated_user_config = admin_policy_utils.apply(dag)
    dag.pre_mount_volumes()
    task = dag.tasks[0]
    assert task.service is not None
    if pool:
        if task.run is not None:
            logger.warning(f'{colorama.Fore.YELLOW}The `run` section will be '
                           f'ignored for pool.{colorama.Style.RESET_ALL}')
        # Use dummy run script for cluster pool.
        task.run = serve_constants.POOL_DUMMY_RUN_COMMAND

    with rich_utils.safe_status(
            ux_utils.spinner_message(f'Initializing {noun}')):
        # Handle file mounts using two-hop approach when cloud storage
        # unavailable
        storage_clouds = (
            storage_lib.get_cached_enabled_storage_cloud_names_or_refresh())
        force_disable_cloud_bucket = skypilot_config.get_nested(
            ('serve', 'force_disable_cloud_bucket'), False)
        if storage_clouds and not force_disable_cloud_bucket:
            controller_utils.maybe_translate_local_file_mounts_and_sync_up(
                task, task_type='serve')
            local_to_controller_file_mounts = {}
        else:
            # Fall back to two-hop file_mount uploading when no cloud storage
            if task.storage_mounts:
                raise exceptions.NotSupportedError(
                    'Cloud-based file_mounts are specified, but no cloud '
                    'storage is available. Please specify local '
                    'file_mounts only.')
            local_to_controller_file_mounts = (
                controller_utils.translate_local_file_mounts_to_two_hop(task))

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
        controller_job_id = None
        if serve_utils.is_consolidation_mode(pool):
            controller_job_id = 0

        vars_to_fill = {
            'remote_task_yaml_path': remote_tmp_task_yaml_path,
            'local_task_yaml_path': service_file.name,
            'service_name': service_name,
            'controller_log_file': controller_log_file,
            'remote_user_config_path': remote_config_yaml_path,
            'local_to_controller_file_mounts': local_to_controller_file_mounts,
            'modified_catalogs':
                service_catalog_common.get_modified_catalog_file_mounts(),
            'consolidation_mode_job_id': controller_job_id,
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
        if not serve_utils.is_consolidation_mode(pool):
            print(f'{colorama.Fore.YELLOW}Launching controller for '
                  f'{service_name!r}...{colorama.Style.RESET_ALL}')
            with common.with_server_user():
                with skypilot_config.local_active_workspace_ctx(
                        constants.SKYPILOT_DEFAULT_WORKSPACE):
                    controller_job_id, controller_handle = execution.launch(
                        task=controller_task,
                        cluster_name=controller_name,
                        retry_until_up=True,
                        _disable_controller_check=True,
                    )
        else:
            controller_type = controller_utils.get_controller_for_pool(pool)
            controller_handle = backend_utils.is_controller_accessible(
                controller=controller_type, stopped_message='')
            backend = backend_utils.get_backend_from_handle(controller_handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            backend.sync_file_mounts(
                handle=controller_handle,
                all_file_mounts=controller_task.file_mounts,
                storage_mounts=controller_task.storage_mounts)
            run_script = controller_task.run
            assert isinstance(run_script, str)
            # Manually add the env variables to the run script. Originally
            # this is done in ray jobs submission but now we have to do it
            # manually because there is no ray runtime on the API server.
            env_cmds = [
                f'export {k}={v!r}' for k, v in controller_task.envs.items()
            ]
            run_script = '\n'.join(env_cmds + [run_script])
            # Dump script for high availability recovery.
            if controller_utils.high_availability_specified(controller_name):
                serve_state.set_ha_recovery_script(service_name, run_script)
            backend.run_on_head(controller_handle, run_script)

        style = colorama.Style
        fore = colorama.Fore

        assert controller_job_id is not None and controller_handle is not None
        # TODO(tian): Cache endpoint locally to speedup. Endpoint won't
        # change after the first time, so there is no consistency issue.
        with rich_utils.safe_status(
                ux_utils.spinner_message(
                    f'Waiting for the {noun} to register')):
            # This function will check the controller job id in the database
            # and return the endpoint if the job id matches. Otherwise it will
            # return None.
            code = serve_utils.ServeCodeGen.wait_service_registration(
                service_name, controller_job_id, pool)
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
                returncode, code, f'Failed to wait for {noun} initialization',
                lb_port_payload)
        except exceptions.CommandError:
            if serve_utils.is_consolidation_mode(pool):
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to wait for {noun} initialization. '
                        'Please check the logs above for more details.'
                    ) from None
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
            if not serve_utils.is_consolidation_mode(pool):
                socket_endpoint = backend_utils.get_endpoints(
                    controller_handle.cluster_name,
                    lb_port,
                    skip_status_check=True).get(lb_port)
            else:
                socket_endpoint = f'localhost:{lb_port}'
            assert socket_endpoint is not None, (
                'Did not get endpoint for controller.')
            # Already checked by validate_service_task
            assert task.service is not None
            protocol = ('http'
                        if task.service.tls_credential is None else 'https')
            socket_endpoint = socket_endpoint.replace('https://', '').replace(
                'http://', '')
            endpoint = f'{protocol}://{socket_endpoint}'

        if pool:
            logger.info(
                f'{fore.CYAN}Pool name: '
                f'{style.BRIGHT}{service_name}{style.RESET_ALL}'
                f'\n📋 Useful Commands'
                f'\n{ux_utils.INDENT_SYMBOL}To submit jobs to the pool:\t'
                f'{ux_utils.BOLD}sky jobs launch --pool {service_name} '
                f'<run-command>{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To submit multiple jobs:\t'
                f'{ux_utils.BOLD}sky jobs launch --pool {service_name} '
                f'--num-jobs 10 <run-command>{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To check the pool status:\t'
                f'{ux_utils.BOLD}sky jobs pool status {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_LAST_SYMBOL}To terminate the pool:\t'
                f'{ux_utils.BOLD}sky jobs pool down {service_name}'
                f'{ux_utils.RESET_BOLD}'
                '\n\n' + ux_utils.finishing_message('Successfully created pool '
                                                    f'{service_name!r}.'))
        else:
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
                '\n\n' + ux_utils.finishing_message(
                    'Service is spinning up and replicas '
                    'will be ready shortly.'))
        return service_name, endpoint


def update(
    task: 'sky.Task',
    service_name: str,
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE,
    pool: bool = False,
) -> None:
    """Updates an existing service or pool."""
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()
    task.validate()
    serve_utils.validate_service_task(task, pool=pool)

    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    # TODO(cblmemo,zhwu): If a user sets a new skypilot_config, the update
    # will not apply the config.
    dag, _ = admin_policy_utils.apply(task)
    task = dag.tasks[0]
    if pool:
        if task.run is not None:
            logger.warning(f'{colorama.Fore.YELLOW}The `run` section will be '
                           f'ignored for pool.{colorama.Style.RESET_ALL}')
        # Use dummy run script for cluster pool.
        task.run = serve_constants.POOL_DUMMY_RUN_COMMAND

    assert task.service is not None
    if not pool and task.service.tls_credential is not None:
        logger.warning('Updating TLS keyfile and certfile is not supported. '
                       'Any updates to the keyfile and certfile will not take '
                       'effect. To update TLS keyfile and certfile, please '
                       'tear down the service and spin up a new one.')

    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
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

    service_record = _get_service_record(service_name, pool, handle, backend)

    if service_record is None:
        cmd = 'sky jobs pool up' if pool else 'sky serve up'
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Cannot find {noun} {service_name!r}.'
                               f'To spin up a {noun}, use {ux_utils.BOLD}'
                               f'{cmd}{ux_utils.RESET_BOLD}')

    prompt = None
    if (service_record['status'] == serve_state.ServiceStatus.CONTROLLER_FAILED
       ):
        prompt = (f'{capnoun} {service_name!r} has a failed controller. '
                  f'Please clean up the {noun} and try again.')
    elif (service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT
         ):
        prompt = (f'{capnoun} {service_name!r} is still initializing '
                  'its controller. Please try again later.')
    if prompt is not None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(prompt)

    if not pool:
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
            ux_utils.spinner_message(f'Initializing {noun}')):
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

    with tempfile.NamedTemporaryFile(
            prefix=f'{service_name}-v{current_version}',
            mode='w') as service_file:
        task_config = task.to_yaml_config()
        common_utils.dump_yaml(service_file.name, task_config)
        remote_task_yaml_path = serve_utils.generate_task_yaml_file_name(
            service_name, current_version, expand_user=False)

        with sky_logging.silent():
            backend.sync_file_mounts(handle,
                                     {remote_task_yaml_path: service_file.name},
                                     storage_mounts=None)

        code = serve_utils.ServeCodeGen.update_service(service_name,
                                                       current_version,
                                                       mode=mode.value,
                                                       pool=pool)
        returncode, _, stderr = backend.run_on_head(handle,
                                                    code,
                                                    require_outputs=True,
                                                    stream_logs=False,
                                                    separate_stderr=True)
        try:
            subprocess_utils.handle_returncode(returncode,
                                               code,
                                               f'Failed to update {noun}s',
                                               stderr,
                                               stream_logs=True)
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

    cmd = 'sky jobs pool status' if pool else 'sky serve status'
    logger.info(
        f'{colorama.Fore.GREEN}{capnoun} {service_name!r} update scheduled.'
        f'{colorama.Style.RESET_ALL}\n'
        f'Please use {ux_utils.BOLD}{cmd} {service_name} '
        f'{ux_utils.RESET_BOLD}to check the latest status.')

    logger.info(
        ux_utils.finishing_message(
            f'Successfully updated {noun} {service_name!r} '
            f'to version {current_version}.'))


def apply(
    task: 'sky.Task',
    service_name: str,
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE,
    pool: bool = False,
) -> None:
    """Applies the config to the service or pool."""
    with filelock.FileLock(serve_utils.get_service_filelock_path(service_name)):
        try:
            controller_type = controller_utils.get_controller_for_pool(pool)
            handle = backend_utils.is_controller_accessible(
                controller=controller_type, stopped_message='')
            backend = backend_utils.get_backend_from_handle(handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            service_record = _get_service_record(service_name, pool, handle,
                                                 backend)
            if service_record is not None:
                return update(task, service_name, mode, pool)
        except exceptions.ClusterNotUpError:
            pass
        up(task, service_name, pool)


def down(
    service_names: Optional[Union[str, List[str]]] = None,
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False,
    pool: bool = False,
) -> None:
    """Tears down a service or pool."""
    noun = 'pool' if pool else 'service'
    if service_names is None:
        service_names = []
    if isinstance(service_names, str):
        service_names = [service_names]
    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=f'All {noun}s should have terminated.')

    service_names_str = ','.join(service_names)
    if sum([bool(service_names), all]) != 1:
        argument_str = (f'{noun}_names={service_names_str}'
                        if service_names else '')
        argument_str += ' all' if all else ''
        raise ValueError(f'Can only specify one of {noun}_names or all. '
                         f'Provided {argument_str!r}.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    service_names = None if all else service_names
    code = serve_utils.ServeCodeGen.terminate_services(service_names, purge,
                                                       pool)

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
                                           f'Failed to terminate {noun}',
                                           stdout)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    logger.info(stdout)


def status(
    service_names: Optional[Union[str, List[str]]] = None,
    pool: bool = False,
) -> List[Dict[str, Any]]:
    """Gets statuses of services or pools."""
    noun = 'pool' if pool else 'service'
    if service_names is not None:
        if isinstance(service_names, str):
            service_names = [service_names]

    try:
        backend_utils.check_network_connection()
    except exceptions.NetworkError as e:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to refresh {noun}s status '
                               'due to network error.') from e

    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=controller_type.value.default_hint_if_non_existent.
        replace('service', noun))

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = serve_utils.ServeCodeGen.get_service_status(service_names, pool=pool)
    returncode, serve_status_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)

    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           f'Failed to fetch {noun}s',
                                           stderr,
                                           stream_logs=True)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    service_records = serve_utils.load_service_status(serve_status_payload)
    # Get the endpoint for each service
    for service_record in service_records:
        service_record['endpoint'] = None
        # Pool doesn't have an endpoint.
        if pool:
            continue
        if service_record['load_balancer_port'] is not None:
            try:
                lb_port = service_record['load_balancer_port']
                if not serve_utils.is_consolidation_mode(pool):
                    endpoint = backend_utils.get_endpoints(
                        cluster=common.SKY_SERVE_CONTROLLER_NAME,
                        port=lb_port).get(lb_port, None)
                else:
                    endpoint = f'localhost:{lb_port}'
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

"""Implementation of the SkyServe core APIs."""
import pathlib
import re
import shlex
import signal
import tempfile
import threading
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

import colorama
import filelock

from sky import backends
from sky import exceptions
from sky import execution
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.catalog import common as service_catalog_common
from sky.data import storage as storage_lib
from sky.serve import constants as serve_constants
from sky.serve import serve_rpc_utils
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.server.requests import request_names
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import admin_policy_utils
from sky.utils import command_runner
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils import yaml_utils

if typing.TYPE_CHECKING:
    import grpc
else:
    grpc = adaptors_common.LazyImport('grpc')

logger = sky_logging.init_logger(__name__)


def _rewrite_tls_credential_paths_and_get_tls_env_vars(
        service_name: str, task: 'task_lib.Task') -> Dict[str, Any]:
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

    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            service_statuses = serve_rpc_utils.RpcRunner.get_service_status(
                handle, [service_name], pool)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
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
    task: 'task_lib.Task',
    service_name: Optional[str] = None,
    pool: bool = False,
) -> Tuple[str, str]:
    """Spins up a service or a pool."""
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
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    dag, mutated_user_config = admin_policy_utils.apply(
        dag, request_name=request_names.AdminPolicyRequestName.SERVE_UP)
    dag.resolve_and_validate_volumes()
    dag.pre_mount_volumes()
    task = dag.tasks[0]
    assert task.service is not None
    if pool:
        # Prevent pool creation from having a run section. Allowing this would
        # not cause any issues, but we want to provide a consistent experience
        # to the user by making it clear that 'setup' runs during creation
        # and 'run' runs during job submission.
        if task.run is not None:
            raise ValueError(
                'Pool creation does not support the `run` section. '
                'During creation the goal is to setup the '
                'environment the jobs will run in.')
        # Use dummy run script for pool.
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
        controller = controller_utils.get_controller_for_pool(pool)
        controller_name = controller.value.cluster_name
        task_config = task.to_yaml_config()
        yaml_utils.dump_yaml(service_file.name, task_config)
        remote_tmp_task_yaml_path = (
            serve_utils.generate_remote_tmp_task_yaml_file_name(service_name))
        remote_config_yaml_path = (
            serve_utils.generate_remote_config_yaml_file_name(service_name))
        controller_log_file = (
            serve_utils.generate_remote_controller_log_file_name(service_name))
        controller_resources = controller_utils.get_controller_resources(
            controller=controller, task_resources=task.resources)
        controller_job_id = None
        if serve_utils.is_consolidation_mode(pool):
            # We need a unique integer per sky.serve.up call to avoid name
            # conflict. Originally in non-consolidation mode, this is the ray
            # job id; now we use the request id hash instead. Here we also
            # make sure it is a 32-bit integer to avoid overflow on sqlalchemy.
            rid = common_utils.get_current_request_id()
            controller_job_id = hash(uuid.UUID(rid).int) & 0x7FFFFFFF

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
            'entrypoint': shlex.quote(common_utils.get_current_command()),
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
        if not pool:
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
                        _request_name=request_names.AdminPolicyRequestName.
                        SERVE_LAUNCH_CONTROLLER,
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
            serve_state.set_ha_recovery_script(service_name, run_script)
            backend.run_on_head(controller_handle, run_script)

        style = colorama.Style
        fore = colorama.Fore

        assert controller_job_id is not None and controller_handle is not None
        assert isinstance(controller_handle, backends.CloudVmRayResourceHandle)
        backend = backend_utils.get_backend_from_handle(controller_handle)
        assert isinstance(backend, backends.CloudVmRayBackend)
        # TODO(tian): Cache endpoint locally to speedup. Endpoint won't
        # change after the first time, so there is no consistency issue.
        try:
            with rich_utils.safe_status(
                    ux_utils.spinner_message(
                        f'Waiting for the {noun} to register')):
                # This function will check the controller job id in the database
                # and return the endpoint if the job id matches. Otherwise it
                # will return None.
                use_legacy = not controller_handle.is_grpc_enabled_with_flag

                if controller_handle.is_grpc_enabled_with_flag:
                    try:
                        lb_port = serve_rpc_utils.RpcRunner.wait_service_registration(  # pylint: disable=line-too-long
                            controller_handle, service_name, controller_job_id,
                            pool)
                    except exceptions.SkyletMethodNotImplementedError:
                        use_legacy = True

                if use_legacy:
                    code = serve_utils.ServeCodeGen.wait_service_registration(
                        service_name, controller_job_id, pool)
                    returncode, lb_port_payload, _ = backend.run_on_head(
                        controller_handle,
                        code,
                        require_outputs=True,
                        stream_logs=False)
                    subprocess_utils.handle_returncode(
                        returncode, code,
                        f'Failed to wait for {noun} initialization',
                        lb_port_payload)
                    lb_port = serve_utils.load_service_initialization_result(
                        lb_port_payload)
        except (exceptions.CommandError, grpc.FutureTimeoutError,
                grpc.RpcError):
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
            if controller_job_status == job_lib.JobStatus.PENDING:
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
            if not serve_utils.is_consolidation_mode(pool) and not pool:
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
                f'<yaml_file>{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To submit multiple jobs:\t'
                f'{ux_utils.BOLD}sky jobs launch --pool {service_name} '
                f'--num-jobs 10 <yaml_file>{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To check the pool status:\t'
                f'{ux_utils.BOLD}sky jobs pool status {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_LAST_SYMBOL}To terminate the pool:\t'
                f'{ux_utils.BOLD}sky jobs pool down {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To update the number of workers:\t'
                f'{ux_utils.BOLD}sky jobs pool apply --pool {service_name} '
                f'--workers 5{ux_utils.RESET_BOLD}'
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
    task: Optional['task_lib.Task'],
    service_name: str,
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE,
    pool: bool = False,
    workers: Optional[int] = None,
) -> None:
    """Updates an existing service or pool."""
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()

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

    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    service_record = _get_service_record(service_name, pool, handle, backend)

    if service_record is None:
        cmd = 'sky jobs pool up' if pool else 'sky serve up'
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Cannot find {noun} {service_name!r}.'
                               f'To spin up a {noun}, use {ux_utils.BOLD}'
                               f'{cmd}{ux_utils.RESET_BOLD}')

    # If task is None and workers is specified, load existing configuration
    # and update replica count.
    if task is None:
        if workers is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Cannot update {noun} without specifying '
                    f'task or workers. Please provide either a task '
                    f'or specify the number of workers.')

        if not pool:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Non-pool service, trying to update replicas to '
                    f'{workers} is not supported. Ignoring the update.')

        # Load the existing task configuration from the service's YAML file
        yaml_content = service_record['yaml_content']

        # Load the existing task configuration
        task = task_lib.Task.from_yaml_str(yaml_content)

        if task.service is None:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('No service configuration found in '
                                   f'existing {noun} {service_name!r}')
        task.set_service(task.service.copy(min_replicas=workers))

        # Clear the run section for pools before validation, since pool updates
        # should only update the number of workers, not the run command. But
        # the run command will have bee set to a dummy command during creation.
        if pool:
            task.run = None

    task.validate()
    serve_utils.validate_service_task(task, pool=pool)

    # Now apply the policy and handle task-specific logic
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    # TODO(cblmemo,zhwu): If a user sets a new skypilot_config, the update
    # will not apply the config.
    dag, _ = admin_policy_utils.apply(
        task, request_name=request_names.AdminPolicyRequestName.SERVE_UPDATE)
    task = dag.tasks[0]
    if pool:
        # Prevent pool creation from having a run section. Allowing this would
        # not cause any issues, but we want to provide a consistent experience
        # to the user by making it clear that 'setup' runs during creation
        # and 'run' runs during job submission.
        if task.run is not None:
            raise ValueError('Pool update does not support the `run` section. '
                             'During update the goal is to setup the '
                             'environment the jobs will run in.')
        # Use dummy run script for pool.
        task.run = serve_constants.POOL_DUMMY_RUN_COMMAND

    assert task.service is not None
    if not pool and task.service.tls_credential is not None:
        logger.warning('Updating TLS keyfile and certfile is not supported. '
                       'Any updates to the keyfile and certfile will not take '
                       'effect. To update TLS keyfile and certfile, please '
                       'tear down the service and spin up a new one.')

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

    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            current_version = serve_rpc_utils.RpcRunner.add_version(
                handle, service_name)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
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
        yaml_utils.dump_yaml(service_file.name, task_config)
        remote_task_yaml_path = serve_utils.generate_task_yaml_file_name(
            service_name, current_version, expand_user=False)

        with sky_logging.silent():
            backend.sync_file_mounts(handle,
                                     {remote_task_yaml_path: service_file.name},
                                     storage_mounts=None)

        use_legacy = not handle.is_grpc_enabled_with_flag

        if not use_legacy:
            try:
                serve_rpc_utils.RpcRunner.update_service(
                    handle, service_name, current_version, mode, pool)
            except exceptions.SkyletMethodNotImplementedError:
                use_legacy = True

        if use_legacy:
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
    task: 'task_lib.Task',
    workers: Optional[int],
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
                return update(task, service_name, mode, pool, workers)
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

    service_names = None if all else service_names

    try:
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        use_legacy = not handle.is_grpc_enabled_with_flag

        if not use_legacy:
            try:
                stdout = serve_rpc_utils.RpcRunner.terminate_services(
                    handle, service_names, purge, pool)
            except exceptions.SkyletMethodNotImplementedError:
                use_legacy = True

        if use_legacy:
            backend = backend_utils.get_backend_from_handle(handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            code = serve_utils.ServeCodeGen.terminate_services(
                service_names, purge, pool)

            returncode, stdout, _ = backend.run_on_head(handle,
                                                        code,
                                                        require_outputs=True,
                                                        stream_logs=False)

            subprocess_utils.handle_returncode(returncode, code,
                                               f'Failed to terminate {noun}',
                                               stdout)
    except exceptions.FetchClusterInfoError as e:
        raise RuntimeError(
            'Failed to fetch controller IP. Please refresh controller status '
            f'by `sky status -r {controller_type.value.cluster_name}` and try '
            'again.') from e
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e
    except grpc.RpcError as e:
        raise RuntimeError(f'{e.details()} ({e.code()})') from e
    except grpc.FutureTimeoutError as e:
        raise RuntimeError('gRPC timed out') from e

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

    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            service_records = serve_rpc_utils.RpcRunner.get_service_status(
                handle, service_names, pool)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        backend = backend_utils.get_backend_from_handle(handle)
        assert isinstance(backend, backends.CloudVmRayBackend)

        code = serve_utils.ServeCodeGen.get_service_status(service_names,
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


ServiceComponentOrStr = Union[str, serve_utils.ServiceComponent]


def tail_logs(
    service_name: str,
    *,
    target: ServiceComponentOrStr,
    replica_id: Optional[int] = None,
    follow: bool = True,
    tail: Optional[int] = None,
    pool: bool = False,
) -> None:
    """Tail logs of a service or pool."""
    if isinstance(target, str):
        target = serve_utils.ServiceComponent(target)

    if pool and target == serve_utils.ServiceComponent.LOAD_BALANCER:
        raise ValueError(f'Target {target} is not supported for pool.')

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

    controller_type = controller_utils.get_controller_for_pool(pool)
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
            follow=follow,
            tail=tail,
            pool=pool)
    else:
        assert replica_id is not None, service_name
        code = serve_utils.ServeCodeGen.stream_replica_logs(service_name,
                                                            replica_id,
                                                            follow,
                                                            tail=tail,
                                                            pool=pool)

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


def _get_all_replica_targets(
        service_name: str, backend: backends.CloudVmRayBackend,
        handle: backends.CloudVmRayResourceHandle,
        pool: bool) -> Set[serve_utils.ServiceComponentTarget]:
    """Helper function to get targets for all live replicas."""
    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            service_records = serve_rpc_utils.RpcRunner.get_service_status(
                handle, [service_name], pool)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
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


def sync_down_logs(
    service_name: str,
    *,
    local_dir: str,
    targets: Union[ServiceComponentOrStr, List[ServiceComponentOrStr],
                   None] = None,
    replica_ids: Optional[List[int]] = None,
    tail: Optional[int] = None,
    pool: bool = False,
) -> str:
    """Sync down logs of a service or pool."""
    noun = 'pool' if pool else 'service'
    repnoun = 'worker' if pool else 'replica'
    caprepnoun = repnoun.capitalize()

    # Step 0) get the controller handle
    with rich_utils.safe_status(
            ux_utils.spinner_message(f'Checking {noun} status...')):
        controller_type = controller_utils.get_controller_for_pool(pool)
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
                ux_utils.spinner_message(f'Getting live {repnoun} infos...')):
            replica_targets = _get_all_replica_targets(service_name, backend,
                                                       handle, pool)
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
                    logger.warning(f'{caprepnoun} ID {target.replica_id} not '
                                   f'found for {service_name}. Skipping...')
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
                    service_name,
                    stream_controller=True,
                    follow=False,
                    tail=tail,
                    pool=pool))
        elif component == serve_utils.ServiceComponent.LOAD_BALANCER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name,
                    stream_controller=False,
                    follow=False,
                    tail=tail,
                    pool=pool))
        elif component == serve_utils.ServiceComponent.REPLICA:
            replica_id = target.replica_id
            assert replica_id is not None, service_name
            stream_logs_code = serve_utils.ServeCodeGen.stream_replica_logs(
                service_name, replica_id, follow=False, tail=tail, pool=pool)
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

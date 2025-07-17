"""SDK functions for managed jobs."""
import os
import pathlib
import tempfile
import typing
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import colorama

from sky import backends
from sky import core
from sky import exceptions
from sky import execution
from sky import global_user_state
from sky import provision as provision_lib
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.backends import backend_utils
from sky.catalog import common as service_catalog_common
from sky.data import storage as storage_lib
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.provision import common as provision_common
from sky.skylet import constants as skylet_constants
from sky.usage import usage_lib
from sky.utils import admin_policy_utils
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils
from sky.workspaces import core as workspaces_core

if typing.TYPE_CHECKING:
    import sky
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)


def _upload_files_to_controller(dag: 'sky.Dag') -> Dict[str, str]:
    """Upload files to the controller.

    In consolidation mode, we still need to upload files to the controller as
    we should keep a separate workdir for each jobs. Assuming two jobs using
    the same workdir, if there are some modifications to the workdir after job 1
    is submitted, on recovery of job 1, the modifications should not be applied.
    """
    local_to_controller_file_mounts: Dict[str, str] = {}

    # For consolidation mode, we don't need to use cloud storage,
    # as uploading to the controller is only a local copy.
    storage_clouds = (
        storage_lib.get_cached_enabled_storage_cloud_names_or_refresh())
    if not managed_job_utils.is_consolidation_mode() and storage_clouds:
        for task_ in dag.tasks:
            controller_utils.maybe_translate_local_file_mounts_and_sync_up(
                task_, task_type='jobs')
    else:
        # We do not have any cloud storage available, so fall back to
        # two-hop file_mount uploading.
        # Note: we can't easily hack sync_storage_mounts() to upload
        # directly to the controller, because the controller may not
        # even be up yet.
        for task_ in dag.tasks:
            if task_.storage_mounts and not storage_clouds:
                # Technically, we could convert COPY storage_mounts that
                # have a local source and do not specify `store`, but we
                # will not do that for now. Only plain file_mounts are
                # supported.
                raise exceptions.NotSupportedError(
                    'Cloud-based file_mounts are specified, but no cloud '
                    'storage is available. Please specify local '
                    'file_mounts only.')

            # Merge file mounts from all tasks.
            local_to_controller_file_mounts.update(
                controller_utils.translate_local_file_mounts_to_two_hop(task_))

    return local_to_controller_file_mounts


def _maybe_submit_job_locally(prefix: str, dag: 'sky.Dag') -> Optional[int]:
    """Submit the managed job locally if in consolidation mode.

    In normal mode the managed job submission is done in the ray job submission.
    For consolidation mode, we need to manually submit it. Check the following
    function for the normal mode submission:
    sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend,
    _exec_code_on_head::_maybe_add_managed_job_code
    """
    if not managed_job_utils.is_consolidation_mode():
        return None

    # Create local directory for the managed job.
    pathlib.Path(prefix).expanduser().mkdir(parents=True, exist_ok=True)
    consolidation_mode_job_id = managed_job_state.set_job_info_without_job_id(
        dag.name,
        workspace=skypilot_config.get_active_workspace(
            force_user_workspace=True),
        entrypoint=common_utils.get_current_command())
    for task_id, task in enumerate(dag.tasks):
        resources_str = backend_utils.get_task_resources_str(
            task, is_managed_job=True)
        managed_job_state.set_pending(consolidation_mode_job_id, task_id,
                                      task.name, resources_str,
                                      task.metadata_json)
    return consolidation_mode_job_id


@timeline.event
@usage_lib.entrypoint
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    stream_logs: bool = True,
) -> Tuple[Optional[int], Optional[backends.ResourceHandle]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Launches a managed job.

    Please refer to sky.cli.job_launch for documentation.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) to launch as a
          managed job.
        name: Name of the managed job.

    Raises:
        ValueError: cluster does not exist. Or, the entrypoint is not a valid
            chain dag.
        sky.exceptions.NotSupportedError: the feature is not supported.
        sky.exceptions.CachedClusterUnavailable: cached jobs controller cluster
            is unavailable

    Returns:
      job_id: Optional[int]; the job ID of the submitted job. None if the
        backend is not CloudVmRayBackend, or no job is submitted to
        the cluster.
      handle: Optional[backends.ResourceHandle]; handle to the controller VM.
        None if dryrun.
    """
    entrypoint = task
    dag_uuid = str(uuid.uuid4().hex[:4])
    dag = dag_utils.convert_entrypoint_to_dag(entrypoint)
    dag.resolve_and_validate_volumes()
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    dag, mutated_user_config = admin_policy_utils.apply(dag)
    if not dag.is_chain():
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Only single-task or chain DAG is '
                             f'allowed for job_launch. Dag: {dag}')
    dag.validate()
    # TODO(aylei): use consolidated job controller instead of performing
    # pre-mount operations when submitting jobs.
    dag.pre_mount_volumes()

    user_dag_str_redacted = dag_utils.dump_chain_dag_to_yaml_str(
        dag, redact_secrets=True)

    dag_utils.maybe_infer_and_fill_dag_and_task_names(dag)

    task_names = set()
    priority = None
    for task_ in dag.tasks:
        if task_.name in task_names:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Task name {task_.name!r} is duplicated in the DAG. '
                    'Either change task names to be unique, or specify the DAG '
                    'name only and comment out the task names (so that they '
                    'will be auto-generated) .')
        task_names.add(task_.name)

        # Check for priority in resources
        task_priority = None
        if task_.resources:
            # Convert set to list to access elements by index
            resources_list = list(task_.resources)
            # Take first resource's priority as reference
            task_priority = resources_list[0].priority

            # Check all other resources have same priority
            for resource in resources_list[1:]:
                if resource.priority != task_priority:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'Task {task_.name!r}: All resources must have the '
                            'same priority. Found priority '
                            f'{resource.priority} but expected {task_priority}.'
                        )

        if task_priority is not None:
            if (priority is not None and priority != task_priority):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Multiple tasks in the DAG have different priorities. '
                        'Either specify a priority in only one task, or set '
                        'the same priority for each task.')
            priority = task_priority

    if priority is None:
        priority = skylet_constants.DEFAULT_PRIORITY

    if (priority < skylet_constants.MIN_PRIORITY or
            priority > skylet_constants.MAX_PRIORITY):
        raise ValueError(
            f'Priority must be between {skylet_constants.MIN_PRIORITY}'
            f' and {skylet_constants.MAX_PRIORITY}, got {priority}')

    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    with rich_utils.safe_status(
            ux_utils.spinner_message('Initializing managed job')):

        # Check whether cached jobs controller cluster is accessible
        cluster_name = (
            controller_utils.Controllers.JOBS_CONTROLLER.value.cluster_name)
        record = global_user_state.get_cluster_from_name(cluster_name)
        if record is not None:
            # there is a cached jobs controller cluster
            try:
                # TODO: do something with returned status?
                _, _ = backend_utils.refresh_cluster_status_handle(
                    cluster_name=cluster_name,
                    force_refresh_statuses=set(status_lib.ClusterStatus),
                    acquire_per_cluster_status_lock=False)
            except (exceptions.ClusterOwnerIdentityMismatchError,
                    exceptions.CloudUserIdentityError,
                    exceptions.ClusterStatusFetchingError) as e:
                # we weren't able to refresh the cluster for its status.
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.CachedClusterUnavailable(
                        f'Cached jobs controller cluster '
                        f'{cluster_name} cannot be refreshed. Please check if '
                        'the cluster is accessible. If the cluster was '
                        'removed, consider removing the cluster from SkyPilot '
                        f'with:\n\n`sky down {cluster_name} --purge`\n\n'
                        f'Reason: {common_utils.format_exception(e)}')

    local_to_controller_file_mounts = _upload_files_to_controller(dag)

    # Has to use `\` to avoid yapf issue.
    with tempfile.NamedTemporaryFile(prefix=f'managed-dag-{dag.name}-',
                                     mode='w') as f, \
         tempfile.NamedTemporaryFile(prefix=f'managed-user-dag-{dag.name}-',
                                     mode='w') as original_user_yaml_path:
        original_user_yaml_path.write(user_dag_str_redacted)
        original_user_yaml_path.flush()

        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        controller = controller_utils.Controllers.JOBS_CONTROLLER
        controller_name = controller.value.cluster_name
        prefix = managed_job_constants.JOBS_TASK_YAML_PREFIX
        remote_original_user_yaml_path = (
            f'{prefix}/{dag.name}-{dag_uuid}.original_user_yaml')
        remote_user_yaml_path = f'{prefix}/{dag.name}-{dag_uuid}.yaml'
        remote_user_config_path = f'{prefix}/{dag.name}-{dag_uuid}.config_yaml'
        remote_env_file_path = f'{prefix}/{dag.name}-{dag_uuid}.env'
        controller_resources = controller_utils.get_controller_resources(
            controller=controller,
            task_resources=sum([list(t.resources) for t in dag.tasks], []))

        consolidation_mode_job_id = _maybe_submit_job_locally(prefix, dag)

        # This is only needed for non-consolidation mode. For consolidation
        # mode, the controller uses the same catalog as API server.
        modified_catalogs = {} if consolidation_mode_job_id is not None else (
            service_catalog_common.get_modified_catalog_file_mounts())

        vars_to_fill = {
            'remote_original_user_yaml_path': remote_original_user_yaml_path,
            'original_user_dag_path': original_user_yaml_path.name,
            'remote_user_yaml_path': remote_user_yaml_path,
            'user_yaml_path': f.name,
            'local_to_controller_file_mounts': local_to_controller_file_mounts,
            'jobs_controller': controller_name,
            # Note: actual cluster name will be <task.name>-<managed job ID>
            'dag_name': dag.name,
            'remote_user_config_path': remote_user_config_path,
            'remote_env_file_path': remote_env_file_path,
            'modified_catalogs': modified_catalogs,
            'priority': priority,
            'consolidation_mode_job_id': consolidation_mode_job_id,
            **controller_utils.shared_controller_vars_to_fill(
                controller,
                remote_user_config_path=remote_user_config_path,
                # TODO(aylei): the mutated config will not be updated
                # afterwards without recreate the controller. Need to
                # revisit this.
                local_user_config=mutated_user_config,
            ),
        }

        yaml_path = os.path.join(
            managed_job_constants.JOBS_CONTROLLER_YAML_PREFIX,
            f'{name}-{dag_uuid}.yaml')
        common_utils.fill_template(
            managed_job_constants.JOBS_CONTROLLER_TEMPLATE,
            vars_to_fill,
            output_path=yaml_path)
        controller_task = task_lib.Task.from_yaml(yaml_path)
        controller_task.set_resources(controller_resources)

        controller_task.managed_job_dag = dag

        logger.info(
            f'{colorama.Fore.YELLOW}'
            f'Launching managed job {dag.name!r} from jobs controller...'
            f'{colorama.Style.RESET_ALL}')

        # Launch with the api server's user hash, so that sky status does not
        # show the owner of the controller as whatever user launched it first.
        with common.with_server_user():
            # Always launch the controller in the default workspace.
            with skypilot_config.local_active_workspace_ctx(
                    skylet_constants.SKYPILOT_DEFAULT_WORKSPACE):
                # TODO(zhwu): the buckets need to be correctly handled for
                # a specific workspace. For example, if a job is launched in
                # workspace A, but the controller is in workspace B, the
                # intermediate bucket and newly created bucket should be in
                # workspace A.
                if consolidation_mode_job_id is None:
                    return execution.launch(task=controller_task,
                                            cluster_name=controller_name,
                                            stream_logs=stream_logs,
                                            retry_until_up=True,
                                            fast=True,
                                            _disable_controller_check=True)
                # Manually launch the scheduler process in consolidation mode.
                local_handle = backend_utils.is_controller_accessible(
                    controller=controller, stopped_message='')
                backend = backend_utils.get_backend_from_handle(local_handle)
                assert isinstance(backend, backends.CloudVmRayBackend)
                backend.sync_file_mounts(
                    handle=local_handle,
                    all_file_mounts=controller_task.file_mounts,
                    storage_mounts=controller_task.storage_mounts)
                run_script = controller_task.run
                assert isinstance(run_script, str)
                # Manually add the env variables to the run script. Originally
                # this is done in ray jobs submission but now we have to do it
                # manually because there is no ray runtime on the API server.
                env_cmds = [
                    f'export {k}={v!r}'
                    for k, v in controller_task.envs.items()
                ]
                run_script = '\n'.join(env_cmds + [run_script])
                # Dump script for high availability recovery.
                if controller_utils.high_availability_specified(
                        controller_name):
                    managed_job_state.set_ha_recovery_script(
                        consolidation_mode_job_id, run_script)
                backend.run_on_head(local_handle, run_script)
                return consolidation_mode_job_id, local_handle


def queue_from_kubernetes_pod(
        pod_name: str,
        context: Optional[str] = None,
        skip_finished: bool = False) -> List[Dict[str, Any]]:
    """Gets the jobs queue from a specific controller pod.

    Args:
        pod_name (str): The name of the controller pod to query for jobs.
        context (Optional[str]): The Kubernetes context to use. If None, the
            current context is used.
        skip_finished (bool): If True, does not return finished jobs.

    Returns:
        [
            {
                'job_id': int,
                'job_name': str,
                'resources': str,
                'submitted_at': (float) timestamp of submission,
                'end_at': (float) timestamp of end,
                'duration': (float) duration in seconds,
                'recovery_count': (int) Number of retries,
                'status': (sky.jobs.ManagedJobStatus) of the job,
                'cluster_resources': (str) resources of the cluster,
                'region': (str) region of the cluster,
            }
        ]

    Raises:
        RuntimeError: If there's an error fetching the managed jobs.
    """
    # Create dummy cluster info to get the command runner.
    provider_config = {'context': context}
    instances = {
        pod_name: [
            provision_common.InstanceInfo(instance_id=pod_name,
                                          internal_ip='',
                                          external_ip='',
                                          tags={})
        ]
    }  # Internal IP is not required for Kubernetes
    cluster_info = provision_common.ClusterInfo(provider_name='kubernetes',
                                                head_instance_id=pod_name,
                                                provider_config=provider_config,
                                                instances=instances)
    managed_jobs_runner = provision_lib.get_command_runners(
        'kubernetes', cluster_info)[0]

    code = managed_job_utils.ManagedJobCodeGen.get_job_table()
    returncode, job_table_payload, stderr = managed_jobs_runner.run(
        code,
        require_outputs=True,
        separate_stderr=True,
        stream_logs=False,
    )
    try:
        subprocess_utils.handle_returncode(returncode,
                                           code,
                                           'Failed to fetch managed jobs',
                                           job_table_payload + stderr,
                                           stream_logs=False)
    except exceptions.CommandError as e:
        raise RuntimeError(str(e)) from e

    jobs = managed_job_utils.load_managed_job_queue(job_table_payload)
    if skip_finished:
        # Filter out the finished jobs. If a multi-task job is partially
        # finished, we will include all its tasks.
        non_finished_tasks = list(
            filter(lambda job: not job['status'].is_terminal(), jobs))
        non_finished_job_ids = {job['job_id'] for job in non_finished_tasks}
        jobs = list(
            filter(lambda job: job['job_id'] in non_finished_job_ids, jobs))
    return jobs


def _maybe_restart_controller(
        refresh: bool, stopped_message: str, spinner_message: str
) -> 'cloud_vm_ray_backend.CloudVmRayResourceHandle':
    """Restart controller if refresh is True and it is stopped."""
    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    if refresh:
        stopped_message = ''
    try:
        handle = backend_utils.is_controller_accessible(
            controller=jobs_controller_type, stopped_message=stopped_message)
    except exceptions.ClusterNotUpError as e:
        if not refresh:
            raise
        handle = None
        controller_status = e.cluster_status

    if handle is not None:
        return handle

    logger.info(f'{colorama.Fore.YELLOW}'
                f'Restarting {jobs_controller_type.value.name}...'
                f'{colorama.Style.RESET_ALL}')

    rich_utils.force_update_status(
        ux_utils.spinner_message(f'{spinner_message} - restarting '
                                 'controller'))
    with skypilot_config.local_active_workspace_ctx(
            skylet_constants.SKYPILOT_DEFAULT_WORKSPACE):
        handle = core.start(
            cluster_name=jobs_controller_type.value.cluster_name)

    controller_status = status_lib.ClusterStatus.UP
    rich_utils.force_update_status(ux_utils.spinner_message(spinner_message))

    assert handle is not None, (controller_status, refresh)
    return handle


@usage_lib.entrypoint
def queue(refresh: bool,
          skip_finished: bool = False,
          all_users: bool = False,
          job_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Gets statuses of managed jobs.

    Please refer to sky.cli.job_queue for documentation.

    Returns:
        [
            {
                'job_id': int,
                'job_name': str,
                'resources': str,
                'submitted_at': (float) timestamp of submission,
                'end_at': (float) timestamp of end,
                'job_duration': (float) duration in seconds,
                'recovery_count': (int) Number of retries,
                'status': (sky.jobs.ManagedJobStatus) of the job,
                'cluster_resources': (str) resources of the cluster,
                'region': (str) region of the cluster,
                'user_name': (Optional[str]) job creator's user name,
                'user_hash': (str) job creator's user hash,
                'task_id': (int), set to 0 (except in pipelines, which may have multiple tasks), # pylint: disable=line-too-long
                'task_name': (str), same as job_name (except in pipelines, which may have multiple tasks), # pylint: disable=line-too-long
            }
        ]
    Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
            does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    handle = _maybe_restart_controller(refresh,
                                       stopped_message='No in-progress '
                                       'managed jobs.',
                                       spinner_message='Checking '
                                       'managed jobs')
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = managed_job_utils.ManagedJobCodeGen.get_job_table()
    returncode, job_table_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)

    if returncode != 0:
        logger.error(job_table_payload + stderr)
        raise RuntimeError('Failed to fetch managed jobs with returncode: '
                           f'{returncode}.\n{job_table_payload + stderr}')

    jobs = managed_job_utils.load_managed_job_queue(job_table_payload)

    if not all_users:

        def user_hash_matches_or_missing(job: Dict[str, Any]) -> bool:
            user_hash = job.get('user_hash', None)
            if user_hash is None:
                # For backwards compatibility, we show jobs that do not have a
                # user_hash. TODO(cooperc): Remove before 0.12.0.
                return True
            return user_hash == common_utils.get_user_hash()

        jobs = list(filter(user_hash_matches_or_missing, jobs))

    accessible_workspaces = workspaces_core.get_workspaces()
    jobs = list(
        filter(
            lambda job: job.get('workspace', skylet_constants.
                                SKYPILOT_DEFAULT_WORKSPACE) in
            accessible_workspaces, jobs))

    if skip_finished:
        # Filter out the finished jobs. If a multi-task job is partially
        # finished, we will include all its tasks.
        non_finished_tasks = list(
            filter(lambda job: not job['status'].is_terminal(), jobs))
        non_finished_job_ids = {job['job_id'] for job in non_finished_tasks}
        jobs = list(
            filter(lambda job: job['job_id'] in non_finished_job_ids, jobs))

    if job_ids:
        jobs = [job for job in jobs if job['job_id'] in job_ids]

    return jobs


@usage_lib.entrypoint
# pylint: disable=redefined-builtin
def cancel(name: Optional[str] = None,
           job_ids: Optional[List[int]] = None,
           all: bool = False,
           all_users: bool = False) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Cancels managed jobs.

    Please refer to sky.cli.job_cancel for documentation.

    Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
        RuntimeError: failed to cancel the job.
    """
    with rich_utils.safe_status(
            ux_utils.spinner_message('Cancelling managed jobs')):
        job_ids = [] if job_ids is None else job_ids
        handle = backend_utils.is_controller_accessible(
            controller=controller_utils.Controllers.JOBS_CONTROLLER,
            stopped_message='All managed jobs should have finished.')

        job_id_str = ','.join(map(str, job_ids))
        if sum([bool(job_ids), name is not None, all or all_users]) != 1:
            arguments = []
            arguments += [f'job_ids={job_id_str}'] if job_ids else []
            arguments += [f'name={name}'] if name is not None else []
            arguments += ['all'] if all else []
            arguments += ['all_users'] if all_users else []
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Can only specify one of JOB_IDS, name, or all/'
                    f'all_users. Provided {" ".join(arguments)!r}.')

        backend = backend_utils.get_backend_from_handle(handle)
        assert isinstance(backend, backends.CloudVmRayBackend)
        if all_users:
            code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(
                None, all_users=True)
        elif all:
            code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(None)
        elif job_ids:
            code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(
                job_ids)
        else:
            assert name is not None, (job_ids, name, all)
            code = managed_job_utils.ManagedJobCodeGen.cancel_job_by_name(name)
        # The stderr is redirected to stdout
        returncode, stdout, stderr = backend.run_on_head(handle,
                                                         code,
                                                         require_outputs=True,
                                                         stream_logs=False)
        try:
            subprocess_utils.handle_returncode(returncode, code,
                                               'Failed to cancel managed job',
                                               stdout + stderr)
        except exceptions.CommandError as e:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(e.error_msg) from e

        logger.info(stdout)
        if 'Multiple jobs found with name' in stdout:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Please specify the job ID instead of the job name.')


@usage_lib.entrypoint
def tail_logs(name: Optional[str],
              job_id: Optional[int],
              follow: bool,
              controller: bool,
              refresh: bool,
              tail: Optional[int] = None) -> int:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Tail logs of managed jobs.

    Please refer to sky.cli.job_logs for documentation.

    Returns:
        Exit code based on success or failure of the job. 0 if success,
        100 if the job failed. See exceptions.JobExitCode for possible exit
        codes.

    Raises:
        ValueError: invalid arguments.
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
    """
    # TODO(zhwu): Automatically restart the jobs controller
    if name is not None and job_id is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cannot specify both name and job_id.')

    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    job_name_or_id_str = ''
    if job_id is not None:
        job_name_or_id_str = str(job_id)
    elif name is not None:
        job_name_or_id_str = f'-n {name}'
    else:
        job_name_or_id_str = ''
    handle = _maybe_restart_controller(
        refresh,
        stopped_message=(
            f'{jobs_controller_type.value.name.capitalize()} is stopped. To '
            f'get the logs, run: {colorama.Style.BRIGHT}sky jobs logs '
            f'-r {job_name_or_id_str}{colorama.Style.RESET_ALL}'),
        spinner_message='Retrieving job logs')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend

    return backend.tail_managed_job_logs(handle,
                                         job_id=job_id,
                                         job_name=name,
                                         follow=follow,
                                         controller=controller,
                                         tail=tail)


@usage_lib.entrypoint
def download_logs(
        name: Optional[str],
        job_id: Optional[int],
        refresh: bool,
        controller: bool,
        local_dir: str = skylet_constants.SKY_LOGS_DIRECTORY) -> Dict[str, str]:
    """Sync down logs of managed jobs.

    Please refer to sky.cli.job_logs for documentation.

    Returns:
        A dictionary mapping job ID to the local path.

    Raises:
        ValueError: invalid arguments.
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
    """
    if name is not None and job_id is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Cannot specify both name and job_id.')

    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    job_name_or_id_str = ''
    if job_id is not None:
        job_name_or_id_str = str(job_id)
    elif name is not None:
        job_name_or_id_str = f'-n {name}'
    else:
        job_name_or_id_str = ''
    handle = _maybe_restart_controller(
        refresh,
        stopped_message=(
            f'{jobs_controller_type.value.name.capitalize()} is stopped. To '
            f'get the logs, run: {colorama.Style.BRIGHT}sky jobs logs '
            f'-r --sync-down {job_name_or_id_str}{colorama.Style.RESET_ALL}'),
        spinner_message='Retrieving job logs')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend

    return backend.sync_down_managed_job_logs(handle,
                                              job_id=job_id,
                                              job_name=name,
                                              controller=controller,
                                              local_dir=local_dir)

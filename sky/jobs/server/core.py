"""SDK functions for managed jobs."""
import os
import signal
import subprocess
import tempfile
import time
import typing
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import colorama

from sky import backends
from sky import core
from sky import exceptions
from sky import execution
from sky import provision as provision_lib
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds.service_catalog import common as service_catalog_common
from sky.data import storage as storage_lib
from sky.jobs import constants as managed_job_constants
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

if typing.TYPE_CHECKING:
    import sky
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)


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
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    dag, mutated_user_config = admin_policy_utils.apply(
        dag, use_mutated_config_in_current_request=False)
    if not dag.is_chain():
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Only single-task or chain DAG is '
                             f'allowed for job_launch. Dag: {dag}')
    dag.validate()
    dag_utils.maybe_infer_and_fill_dag_and_task_names(dag)

    task_names = set()
    for task_ in dag.tasks:
        if task_.name in task_names:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Task name {task_.name!r} is duplicated in the DAG. '
                    'Either change task names to be unique, or specify the DAG '
                    'name only and comment out the task names (so that they '
                    'will be auto-generated) .')
        task_names.add(task_.name)

    dag_utils.fill_default_config_in_dag_for_job_launch(dag)

    with rich_utils.safe_status(
            ux_utils.spinner_message('Initializing managed job')):

        local_to_controller_file_mounts = {}

        if storage_lib.get_cached_enabled_storage_cloud_names_or_refresh():
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
                if task_.storage_mounts:
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
                    controller_utils.translate_local_file_mounts_to_two_hop(
                        task_))

    with tempfile.NamedTemporaryFile(prefix=f'managed-dag-{dag.name}-',
                                     mode='w') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        controller = controller_utils.Controllers.JOBS_CONTROLLER
        controller_name = controller.value.cluster_name
        prefix = managed_job_constants.JOBS_TASK_YAML_PREFIX
        remote_user_yaml_path = f'{prefix}/{dag.name}-{dag_uuid}.yaml'
        remote_user_config_path = f'{prefix}/{dag.name}-{dag_uuid}.config_yaml'
        remote_env_file_path = f'{prefix}/{dag.name}-{dag_uuid}.env'
        controller_resources = controller_utils.get_controller_resources(
            controller=controller_utils.Controllers.JOBS_CONTROLLER,
            task_resources=sum([list(t.resources) for t in dag.tasks], []))

        vars_to_fill = {
            'remote_user_yaml_path': remote_user_yaml_path,
            'user_yaml_path': f.name,
            'local_to_controller_file_mounts': local_to_controller_file_mounts,
            'jobs_controller': controller_name,
            # Note: actual cluster name will be <task.name>-<managed job ID>
            'dag_name': dag.name,
            'remote_user_config_path': remote_user_config_path,
            'remote_env_file_path': remote_env_file_path,
            'modified_catalogs':
                service_catalog_common.get_modified_catalog_file_mounts(),
            'dashboard_setup_cmd': managed_job_constants.DASHBOARD_SETUP_CMD,
            'dashboard_user_id': common.SERVER_ID,
            **controller_utils.shared_controller_vars_to_fill(
                controller_utils.Controllers.JOBS_CONTROLLER,
                remote_user_config_path=remote_user_config_path,
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

        sky_logging.print(
            f'{colorama.Fore.YELLOW}'
            f'Launching managed job {dag.name!r} from jobs controller...'
            f'{colorama.Style.RESET_ALL}')

        # Launch with the api server's user hash, so that sky status does not
        # show the owner of the controller as whatever user launched it first.
        with common.with_server_user_hash():
            return execution.launch(task=controller_task,
                                    cluster_name=controller_name,
                                    stream_logs=stream_logs,
                                    idle_minutes_to_autostop=skylet_constants.
                                    CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
                                    retry_until_up=True,
                                    fast=True,
                                    _disable_controller_check=True)


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

    sky_logging.print(f'{colorama.Fore.YELLOW}'
                      f'Restarting {jobs_controller_type.value.name}...'
                      f'{colorama.Style.RESET_ALL}')

    rich_utils.force_update_status(
        ux_utils.spinner_message(f'{spinner_message} - restarting '
                                 'controller'))
    handle = core.start(cluster_name=jobs_controller_type.value.cluster_name)
    # Make sure the dashboard is running when the controller is restarted.
    # We should not directly use execution.launch() and have the dashboard cmd
    # in the task setup because since we are using detached_setup, it will
    # become a job on controller which messes up the job IDs (we assume the
    # job ID in controller's job queue is consistent with managed job IDs).
    with rich_utils.safe_status(
            ux_utils.spinner_message('Starting dashboard...')):
        runner = handle.get_command_runners()[0]
        runner.run(
            f'export '
            f'{skylet_constants.USER_ID_ENV_VAR}={common.SERVER_ID!r}; '
            f'{managed_job_constants.DASHBOARD_SETUP_CMD}',
            stream_logs=True,
        )
    controller_status = status_lib.ClusterStatus.UP
    rich_utils.force_update_status(ux_utils.spinner_message(spinner_message))

    assert handle is not None, (controller_status, refresh)
    return handle


@usage_lib.entrypoint
def queue(refresh: bool,
          skip_finished: bool = False,
          all_users: bool = False) -> List[Dict[str, Any]]:
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
                'duration': (float) duration in seconds,
                'recovery_count': (int) Number of retries,
                'status': (sky.jobs.ManagedJobStatus) of the job,
                'cluster_resources': (str) resources of the cluster,
                'region': (str) region of the cluster,
                'user_name': (Optional[str]) job creator's user name,
                'user_hash': (str) job creator's user hash,
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
                           f'{returncode}')

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

    if skip_finished:
        # Filter out the finished jobs. If a multi-task job is partially
        # finished, we will include all its tasks.
        non_finished_tasks = list(
            filter(lambda job: not job['status'].is_terminal(), jobs))
        non_finished_job_ids = {job['job_id'] for job in non_finished_tasks}
        jobs = list(
            filter(lambda job: job['job_id'] in non_finished_job_ids, jobs))

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
            raise ValueError('Can only specify one of JOB_IDS, name, or all/'
                             f'all_users. Provided {" ".join(arguments)!r}.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    if all_users:
        code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(
            None, all_users=True)
    elif all:
        code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(None)
    elif job_ids:
        code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(job_ids)
    else:
        assert name is not None, (job_ids, name, all)
        code = managed_job_utils.ManagedJobCodeGen.cancel_job_by_name(name)
    # The stderr is redirected to stdout
    returncode, stdout, _ = backend.run_on_head(handle,
                                                code,
                                                require_outputs=True,
                                                stream_logs=False)
    try:
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to cancel managed job',
                                           stdout)
    except exceptions.CommandError as e:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(e.error_msg) from e

    sky_logging.print(stdout)
    if 'Multiple jobs found with name' in stdout:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Please specify the job ID instead of the job name.')


@usage_lib.entrypoint
def tail_logs(name: Optional[str], job_id: Optional[int], follow: bool,
              controller: bool, refresh: bool) -> int:
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
                                         controller=controller)


def start_dashboard_forwarding(refresh: bool = False) -> Tuple[int, int]:
    """Opens a dashboard for managed jobs (needs controller to be UP)."""
    # TODO(SKY-1212): ideally, the controller/dashboard server should expose the
    # API perhaps via REST. Then here we would (1) not have to use SSH to try to
    # see if the controller is UP first, which is slow; (2) not have to run SSH
    # port forwarding first (we'd just launch a local dashboard which would make
    # REST API calls to the controller dashboard server).
    logger.info('Starting dashboard')
    hint = ('Dashboard is not available if jobs controller is not up. Run '
            'a managed job first or run: sky jobs queue --refresh')
    handle = _maybe_restart_controller(
        refresh=refresh,
        stopped_message=hint,
        spinner_message='Checking jobs controller')

    # SSH forward a free local port to remote's dashboard port.
    remote_port = skylet_constants.SPOT_DASHBOARD_REMOTE_PORT
    free_port = common_utils.find_free_port(remote_port)
    runner = handle.get_command_runners()[0]
    port_forward_command = ' '.join(
        runner.port_forward_command(port_forward=[(free_port, remote_port)],
                                    connect_timeout=1))
    port_forward_command = (
        f'{port_forward_command} '
        f'> ~/sky_logs/api_server/dashboard-{common_utils.get_user_hash()}.log '
        '2>&1')
    logger.info(f'Forwarding port: {colorama.Style.DIM}{port_forward_command}'
                f'{colorama.Style.RESET_ALL}')

    ssh_process = subprocess.Popen(port_forward_command,
                                   shell=True,
                                   start_new_session=True)
    time.sleep(3)  # Added delay for ssh_command to initialize.
    logger.info(f'{colorama.Fore.GREEN}Dashboard is now available at: '
                f'http://127.0.0.1:{free_port}{colorama.Style.RESET_ALL}')

    return free_port, ssh_process.pid


def stop_dashboard_forwarding(pid: int) -> None:
    # Exit the ssh command when the context manager is closed.
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        # This happens if jobs controller is auto-stopped.
        pass
    logger.info('Forwarding port closed. Exiting.')


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

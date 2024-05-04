"""SDK functions for managed jobs."""
import os
import tempfile
from typing import Any, Dict, List, Optional, Union
import uuid

import colorama

import sky
from sky import backends
from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky import task as task_lib
from sky.backends import backend_utils
from sky.clouds.service_catalog import common as service_catalog_common
from sky.jobs import constants as managed_job_constants
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants as skylet_constants
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils


@usage_lib.entrypoint
def launch(
    task: Union['sky.Task', 'sky.Dag'],
    name: Optional[str] = None,
    stream_logs: bool = True,
    detach_run: bool = False,
    retry_until_up: bool = False,
) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Launch a managed job.

    Please refer to sky.cli.job_launch for documentation.

    Args:
        task: sky.Task, or sky.Dag (experimental; 1-task only) to launch as a
          managed job.
        name: Name of the managed job.
        detach_run: Whether to detach the run.

    Raises:
        ValueError: cluster does not exist. Or, the entrypoint is not a valid
            chain dag.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    entrypoint = task
    dag_uuid = str(uuid.uuid4().hex[:4])

    dag = dag_utils.convert_entrypoint_to_dag(entrypoint)
    if not dag.is_chain():
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Only single-task or chain DAG is '
                             f'allowed for job_launch. Dag: {dag}')

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

    for task_ in dag.tasks:
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task_, path='jobs')

    with tempfile.NamedTemporaryFile(prefix=f'managed-dag-{dag.name}-',
                                     mode='w') as f:
        dag_utils.dump_chain_dag_to_yaml(dag, f.name)
        controller = controller_utils.Controllers.JOBS_CONTROLLER
        controller_name = controller.value.cluster_name
        prefix = managed_job_constants.JOBS_TASK_YAML_PREFIX
        remote_user_yaml_path = f'{prefix}/{dag.name}-{dag_uuid}.yaml'
        remote_user_config_path = f'{prefix}/{dag.name}-{dag_uuid}.config_yaml'
        controller_resources = controller_utils.get_controller_resources(
            controller=controller_utils.Controllers.JOBS_CONTROLLER,
            task_resources=sum([list(t.resources) for t in dag.tasks], []))

        vars_to_fill = {
            'remote_user_yaml_path': remote_user_yaml_path,
            'user_yaml_path': f.name,
            'jobs_controller': controller_name,
            # Note: actual cluster name will be <task.name>-<managed job ID>
            'dag_name': dag.name,
            'retry_until_up': retry_until_up,
            'remote_user_config_path': remote_user_config_path,
            'sky_python_cmd': skylet_constants.SKY_PYTHON_CMD,
            'modified_catalogs':
                service_catalog_common.get_modified_catalog_file_mounts(),
            **controller_utils.shared_controller_vars_to_fill(
                controller_utils.Controllers.JOBS_CONTROLLER,
                remote_user_config_path=remote_user_config_path,
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
        assert len(controller_task.resources) == 1, controller_task

        sky_logging.print(
            f'{colorama.Fore.YELLOW}'
            f'Launching managed job {dag.name!r} from jobs controller...'
            f'{colorama.Style.RESET_ALL}')
        sky_logging.print('Launching jobs controller...')
        sky.launch(task=controller_task,
                   stream_logs=stream_logs,
                   cluster_name=controller_name,
                   detach_run=detach_run,
                   idle_minutes_to_autostop=skylet_constants.
                   CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP,
                   retry_until_up=True,
                   _disable_controller_check=True)


@usage_lib.entrypoint
def queue(refresh: bool, skip_finished: bool = False) -> List[Dict[str, Any]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Get statuses of managed jobs.

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
            }
        ]
    Raises:
        sky.exceptions.ClusterNotUpError: the jobs controller is not up or
            does not exist.
        RuntimeError: if failed to get the managed jobs with ssh.
    """
    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    stopped_message = ''
    if not refresh:
        stopped_message = 'No in-progress managed jobs.'
    try:
        handle = backend_utils.is_controller_accessible(
            controller=jobs_controller_type, stopped_message=stopped_message)
    except exceptions.ClusterNotUpError as e:
        if not refresh:
            raise
        handle = None
        controller_status = e.cluster_status

    if refresh and handle is None:
        sky_logging.print(f'{colorama.Fore.YELLOW}'
                          'Restarting controller for latest status...'
                          f'{colorama.Style.RESET_ALL}')

        rich_utils.force_update_status(
            '[cyan] Checking managed jobs - restarting '
            'controller[/]')
        handle = sky.start(jobs_controller_type.value.cluster_name)
        controller_status = status_lib.ClusterStatus.UP
        rich_utils.force_update_status('[cyan] Checking managed jobs[/]')

    assert handle is not None, (controller_status, refresh)

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = managed_job_utils.ManagedJobCodeGen.get_job_table()
    returncode, job_table_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)

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


@usage_lib.entrypoint
# pylint: disable=redefined-builtin
def cancel(name: Optional[str] = None,
           job_ids: Optional[List[int]] = None,
           all: bool = False) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Cancel managed jobs.

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
    if sum([len(job_ids) > 0, name is not None, all]) != 1:
        argument_str = f'job_ids={job_id_str}' if len(job_ids) > 0 else ''
        argument_str += f' name={name}' if name is not None else ''
        argument_str += ' all' if all else ''
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Can only specify one of JOB_IDS or name or all. '
                             f'Provided {argument_str!r}.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    if all:
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
def tail_logs(name: Optional[str], job_id: Optional[int], follow: bool) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Tail logs of managed jobs.

    Please refer to sky.cli.job_logs for documentation.

    Raises:
        ValueError: invalid arguments.
        sky.exceptions.ClusterNotUpError: the jobs controller is not up.
    """
    # TODO(zhwu): Automatically restart the jobs controller
    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    handle = backend_utils.is_controller_accessible(
        controller=jobs_controller_type,
        stopped_message=(
            'Please restart the jobs controller with '
            f'`sky start {jobs_controller_type.value.cluster_name}`.'))

    if name is not None and job_id is not None:
        raise ValueError('Cannot specify both name and job_id.')
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend
    # Stream the realtime logs
    backend.tail_managed_job_logs(handle,
                                  job_id=job_id,
                                  job_name=name,
                                  follow=follow)


spot_launch = common_utils.deprecated_function(
    launch,
    name='sky.jobs.launch',
    deprecated_name='spot_launch',
    removing_version='0.8.0',
    override_argument={'use_spot': True})
spot_queue = common_utils.deprecated_function(queue,
                                              name='sky.jobs.queue',
                                              deprecated_name='spot_queue',
                                              removing_version='0.8.0')
spot_cancel = common_utils.deprecated_function(cancel,
                                               name='sky.jobs.cancel',
                                               deprecated_name='spot_cancel',
                                               removing_version='0.8.0')
spot_tail_logs = common_utils.deprecated_function(
    tail_logs,
    name='sky.jobs.tail_logs',
    deprecated_name='spot_tail_logs',
    removing_version='0.8.0')

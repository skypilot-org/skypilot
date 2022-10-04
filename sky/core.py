"""SDK functions for cluster/job management."""
import colorama
import getpass
from typing import Any, Dict, List, Optional, Tuple

from sky import dag
from sky import task
from sky import backends
from sky import data
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import spot
from sky.backends import backend_utils
from sky.backends import onprem_utils
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import tpu_utils
from sky.utils import ux_utils
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

# ======================
# = Cluster Management =
# ======================


# pylint: disable=redefined-builtin
def status(all: bool, refresh: bool) -> List[Dict[str, Any]]:
    """Get the cluster status in dict.

    Please refer to the sky.cli.status for the document.

    Returns:
        A list of dicts, each dict contains the information of a cluster.

        [
            {
                'name': (str) cluster name,
                'launched_at': (int) timestamp of launched,
                'last_use': (int) timestamp of last use,
                'status': (sky.ClusterStatus) cluster status,
                'autostop': (int) idle time before autostop,
                'metadata': (dict) metadata of the cluster,
            }
        ]

    """
    cluster_records = backend_utils.get_clusters(all, refresh)
    return cluster_records


def _start(cluster_name: str,
           idle_minutes_to_autostop: Optional[int] = None,
           retry_until_up: bool = False) -> backends.Backend.ResourceHandle:

    cluster_status, handle = backend_utils.refresh_cluster_status_handle(
        cluster_name)
    if cluster_status == global_user_state.ClusterStatus.UP:
        print(f'Cluster {cluster_name!r} is already up.')
        return
    assert cluster_status in (
        global_user_state.ClusterStatus.INIT,
        global_user_state.ClusterStatus.STOPPED), cluster_status

    backend = backend_utils.get_backend_from_handle(handle)
    if not isinstance(backend, backends.CloudVmRayBackend):
        raise exceptions.NotSupportedError(
            f'Starting cluster {cluster_name!r} with backend {backend.NAME} '
            'is not supported.')
    with dag.Dag():
        dummy_task = task.Task().set_resources(handle.launched_resources)
        dummy_task.num_nodes = handle.launched_nodes
    handle = backend.provision(dummy_task,
                               to_provision=handle.launched_resources,
                               dryrun=False,
                               stream_logs=True,
                               cluster_name=cluster_name,
                               retry_until_up=retry_until_up)
    if idle_minutes_to_autostop is not None:
        backend.set_autostop(handle, idle_minutes_to_autostop)
    return handle


def start(cluster_name: str,
          idle_minutes_to_autostop: Optional[int] = None,
          retry_until_up: bool = False):
    """Start the cluster.

    Please refer to the sky.cli.start for the document.

    Raises:
        sky.exceptions.NotSupportedError: the cluster is not supported.
    """
    _start(cluster_name, idle_minutes_to_autostop, retry_until_up)


def stop(cluster_name: str, purge: bool = False):
    """Stop the cluster

    Please refer to the sky.cli.stop for the document.

    Raises:
        ValueError: cluster does not exist.
        RuntimeError: Fail to stop the cluster.
        sky.exceptions.NotSupportedError: the cluster is not supported.
    """
    if cluster_name in backend_utils.SKY_RESERVED_CLUSTER_NAMES:
        raise exceptions.NotSupportedError(
            f'Stopping sky reserved cluster {cluster_name!r} '
            f'is not supported.')
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise ValueError(f'Cluster {cluster_name!r} does not exist.')
    if tpu_utils.is_tpu_vm_pod(handle.launched_resources):
        # Reference:
        # https://cloud.google.com/tpu/docs/managing-tpus-tpu-vm#stopping_a_with_gcloud  # pylint: disable=line-too-long
        raise exceptions.NotSupportedError(
            f'Stopping cluster {cluster_name!r} with TPU VM Pod '
            'is not supported.')

    backend = backend_utils.get_backend_from_handle(handle)
    if (isinstance(backend, backends.CloudVmRayBackend) and
            handle.launched_resources.use_spot):
        # Disable spot instances to be stopped.
        # TODO(suquark): enable GCP+spot to be stopped in the future.
        raise exceptions.NotSupportedError(
            f'{colorama.Fore.YELLOW}Stopping cluster '
            f'{cluster_name!r}... skipped.{colorama.Style.RESET_ALL}\n'
            '  Stopping spot instances is not supported as the attached '
            'disks will be lost.\n'
            '  To terminate the cluster instead, run: '
            f'{colorama.Style.BRIGHT}sky down {cluster_name}')
    backend.teardown(handle, terminate=False, purge=purge)


def down(cluster_name: str, purge: bool = False):
    """Down the cluster

    Please refer to the sky.cli.down for the document.

    Raises:
        ValueError: cluster does not exist.
        sky.exceptions.NotSupportedError: the cluster is not supported.
    """
    if (cluster_name in backend_utils.SKY_RESERVED_CLUSTER_NAMES and not purge):
        raise exceptions.NotSupportedError(
            f'Tearing down sky reserved cluster {cluster_name!r} '
            f'is not supported.')
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise ValueError(f'Cluster {cluster_name!r} does not exist.')

    backend = backend_utils.get_backend_from_handle(handle)
    backend.teardown(handle, terminate=True, purge=purge)


def autostop(cluster_name: str, idle_minutes_to_autostop: int):
    """Set the autostop time of the cluster.

    Please refer to the sky.cli.autostop for the document.

    Args:
        cluster_name (str): name of the cluster.
        autostop_time (int): autostop time in minutes.
    Raises:
        ValueError: cluster does not exist.
        sky.exceptions.NotSupportedError: the cluster is not supported.
        sky.exceptions.ClusterNotUpError: the cluster is not UP.
    """
    verb = 'Scheduling' if idle_minutes_to_autostop >= 0 else 'Cancelling'
    operation = f'{verb} auto-stop on'
    if cluster_name in backend_utils.SKY_RESERVED_CLUSTER_NAMES:
        raise exceptions.NotSupportedError(
            f'{operation} sky reserved cluster {cluster_name!r} '
            f'is not supported.')
    (cluster_status,
     handle) = backend_utils.refresh_cluster_status_handle(cluster_name)
    if handle is None:
        raise ValueError(f'Cluster {cluster_name!r} does not exist.')
    if tpu_utils.is_tpu_vm_pod(handle.launched_resources):
        # Reference:
        # https://cloud.google.com/tpu/docs/managing-tpus-tpu-vm#stopping_a_with_gcloud  # pylint: disable=line-too-long
        raise exceptions.NotSupportedError(
            f'{operation} cluster {cluster_name!r} with TPU VM Pod '
            'is not supported.')

    backend = backend_utils.get_backend_from_handle(handle)
    if not isinstance(backend, backends.CloudVmRayBackend):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                f'{colorama.Fore.YELLOW}{operation} cluster '
                f'{cluster_name!r}... skipped{colorama.Style.RESET_ALL}'
                '\n  Auto-stopping is only supported by backend: '
                f'{backends.CloudVmRayBackend.NAME}')
    if cluster_status != global_user_state.ClusterStatus.UP:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'{colorama.Fore.YELLOW}{operation} cluster '
                f'{cluster_name!r} (status: {cluster_status.value})... skipped'
                f'{colorama.Style.RESET_ALL}'
                '\n  Auto-stop can only be set/unset for '
                f'{global_user_state.ClusterStatus.UP.value} clusters.')
    backend.set_autostop(handle, idle_minutes_to_autostop)


# ==================
# = Job Management =
# ==================


def _check_cluster_available(cluster_name: str,
                             operation: str) -> backends.Backend.ResourceHandle:
    """Check if the cluster is available."""
    cluster_status, handle = backend_utils.refresh_cluster_status_handle(
        cluster_name)
    if handle is None:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'{colorama.Fore.YELLOW}Cluster {cluster_name!r} does not '
                f'exist.{colorama.Style.RESET_ALL} skipped.')
    backend = backend_utils.get_backend_from_handle(handle)
    if isinstance(backend, backends.LocalDockerBackend):
        # LocalDockerBackend does not support job queues
        raise exceptions.NotSupportedError(
            f'Cluster {cluster_name} with LocalDockerBackend does '
            f'not support {operation}.')
    if cluster_status != global_user_state.ClusterStatus.UP:
        if onprem_utils.check_if_local_cloud(cluster_name):
            raise exceptions.ClusterNotUpError(
                constants.UNINITIALIZED_ONPREM_CLUSTER_MESSAGE.format(
                    cluster_name))
        raise exceptions.ClusterNotUpError(
            f'{colorama.Fore.YELLOW}Cluster {cluster_name!r} is not up '
            f'(status: {cluster_status.value}); skipped.'
            f'{colorama.Style.RESET_ALL}')

    if handle.head_ip is None:
        raise exceptions.ClusterNotUpError(
            f'Cluster {cluster_name!r} has been stopped or not properly set up.'
            ' Please re-launch it with `sky start`.')
    return handle


def queue(cluster_name: str,
          skip_finished: bool = False,
          all_users: bool = False) -> List[dict]:
    """Get the job queue in List[dict].

    Please refer to the sky.cli.queue for the document.

    Returns:
        List[dict]:
        [
            {
                'job_id': (int) job id,
                'job_name': (str) job name,
                'username': (str) username,
                'submitted_at': (int) timestamp of submitted,
                'start_at': (int) timestamp of started,
                'end_at': (int) timestamp of ended,
                'resources': (str) resources,
                'status': (job_lib.JobStatus) job status,
                'log_path': (str) log path,
            }
        ]
    raises:
        RuntimeError: if failed to get the job queue.
        sky.exceptions.ClusterNotUpError: the cluster is not up.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    all_jobs = not skip_finished
    username = getpass.getuser()
    if all_users:
        username = None
    code = job_lib.JobLibCodeGen.get_job_queue(username, all_jobs)

    handle = _check_cluster_available(cluster_name, 'getting the job queue')
    backend = backend_utils.get_backend_from_handle(handle)

    returncode, jobs_payload, stderr = backend.run_on_head(handle,
                                                           code,
                                                           require_outputs=True,
                                                           separate_stderr=True)
    if returncode != 0:
        raise RuntimeError(f'{jobs_payload + stderr}\n{colorama.Fore.RED}'
                           f'Failed to get job queue on cluster {cluster_name}.'
                           f'{colorama.Style.RESET_ALL}')
    jobs = job_lib.load_job_queue(jobs_payload)
    return jobs


# pylint: disable=redefined-builtin
def cancel(cluster_name: str,
           all: bool = False,
           job_ids: Optional[List[int]] = None):
    """Cancel jobs.

    Please refer to the sky.cli.cancel for the document.

    Raises:
        ValueError: arguments are invalid or the cluster is not supported.
        sky.exceptions.ClusterNotUpError: the cluster is not up.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    job_ids = [] if job_ids is None else job_ids
    if len(job_ids) == 0 and not all:
        raise ValueError(
            'sky cancel requires either a job id '
            f'(see `sky queue {cluster_name} -s`) or the --all flag.')

    backend_utils.check_cluster_name_not_reserved(
        cluster_name, operation_str='Cancelling jobs')

    # Check the status of the cluster.
    handle = _check_cluster_available(cluster_name, 'cancelling jobs')
    backend = backend_utils.get_backend_from_handle(handle)

    if all:
        print(f'{colorama.Fore.YELLOW}'
              f'Cancelling all jobs on cluster {cluster_name!r}...'
              f'{colorama.Style.RESET_ALL}')
        job_ids = None
    else:
        jobs_str = ', '.join(map(str, job_ids))
        print(f'{colorama.Fore.YELLOW}'
              f'Cancelling jobs ({jobs_str}) on cluster {cluster_name!r}...'
              f'{colorama.Style.RESET_ALL}')

    backend.cancel_jobs(handle, job_ids)


def tail_logs(cluster_name: str, job_id: Optional[str], follow: bool = True):
    """Tail the logs of a job.

    Please refer to the sky.cli.tail_logs for the document.

    Raises:
        ValueError: arguments are invalid or the cluster is not supported.
        sky.exceptions.ClusterNotUpError: the cluster is not up.
        sky.exceptions.NotSupportedError: the feature is not supported.
    """
    # Check the status of the cluster.
    handle = _check_cluster_available(cluster_name, 'tailing logs')
    backend = backend_utils.get_backend_from_handle(handle)

    job_str = f'job {job_id}'
    if job_id is None:
        job_str = 'the last job'
    print(f'{colorama.Fore.YELLOW}'
          f'Tailing logs of {job_str} on cluster {cluster_name!r}...'
          f'{colorama.Style.RESET_ALL}')

    backend.tail_logs(handle, job_id, follow=follow)


def download_logs(
        cluster_name: str,
        job_ids: Optional[List[str]],
        local_dir: str = constants.SKY_LOGS_DIRECTORY) -> Dict[str, str]:
    """Download the logs of jobs.

    Args:
        cluster_name: (str) name of the cluster.
        job_ids: (List[str]) job ids.
    Returns:
        Dict[str, str]: a mapping of job_id to local log path.
    """
    # Check the status of the cluster.
    handle = _check_cluster_available(cluster_name, 'downloading logs')
    backend = backend_utils.get_backend_from_handle(handle)

    if job_ids is not None and len(job_ids) == 0:
        return []

    print(f'{colorama.Fore.YELLOW}'
          'Syncing down logs to local...'
          f'{colorama.Style.RESET_ALL}')
    local_log_dirs = backend.sync_down_logs(handle, job_ids, local_dir)
    return local_log_dirs


def job_status(
        cluster_name: str,
        job_ids: Optional[List[str]],
        stream_logs: bool = False) -> Dict[str, Optional[job_lib.JobStatus]]:
    """Get the status of jobs.

    Args:
        cluster_name: (str) name of the cluster.
        job_ids: (List[str]) job ids. If None, get the status of the last job.
    Returns:
        Dict[str, Optional[job_lib.JobStatus]]: A mapping of job_id to job
        statuses. The status will be None if the job does not exist.
        If job_ids is None and there is no job on the cluster, it will return
        {None: None}.
    """
    # Check the status of the cluster.
    handle = _check_cluster_available(cluster_name, 'getting job status')
    backend = backend_utils.get_backend_from_handle(handle)

    if job_ids is not None and len(job_ids) == 0:
        return []

    print(f'{colorama.Fore.YELLOW}'
          'Getting job status...'
          f'{colorama.Style.RESET_ALL}')

    statuses = backend.get_job_status(handle, job_ids, stream_logs=stream_logs)
    return statuses


# =======================
# = Spot Job Management =
# =======================


def _is_spot_controller_up(
    stopped_message: str,
) -> Tuple[Optional[global_user_state.ClusterStatus],
           Optional[backends.Backend.ResourceHandle]]:
    controller_status, handle = backend_utils.refresh_cluster_status_handle(
        spot.SPOT_CONTROLLER_NAME, force_refresh=True)
    if controller_status is None:
        print('No managed spot job has been run.')
    elif controller_status != global_user_state.ClusterStatus.UP:
        msg = (f'Spot controller {spot.SPOT_CONTROLLER_NAME} '
               f'is {controller_status.value}.')
        if controller_status == global_user_state.ClusterStatus.STOPPED:
            msg += f'\n{stopped_message}'
        if controller_status == global_user_state.ClusterStatus.INIT:
            msg += '\nPlease wait for the controller to be ready.'
        print(msg)
        handle = None
    return controller_status, handle


def spot_status(refresh: bool) -> List[Dict[str, Any]]:
    """Get statuses of managed spot jobs.

    Please refer to the sky.cli.spot_status for the document.

    Returns:
        [
            {
                'job_id': int,
                'job_name': str,
                'resources': str,
                'submitted_at': (float) timestamp of submission,
                'end_at': (float) timestamp of end,
                'duration': (float) duration in seconds,
                'retry_count': int Number of retries,
                'status': sky.JobStatus status of the job,
                'cluster_resources': (str) resources of the cluster,
                'region': (str) region of the cluster,
            }
        ]
    Raises:
        sky.exceptions.ClusterNotUpError: the spot controller is not up.
    """

    stop_msg = ''
    if not refresh:
        stop_msg = 'To view the latest job table: sky spot status --refresh'
    controller_status, handle = _is_spot_controller_up(stop_msg)

    if controller_status is None:
        return []

    if (refresh and controller_status in [
            global_user_state.ClusterStatus.STOPPED,
            global_user_state.ClusterStatus.INIT
    ]):
        print(f'{colorama.Fore.YELLOW}'
              'Restarting controller for latest status...'
              f'{colorama.Style.RESET_ALL}')

        handle = _start(spot.SPOT_CONTROLLER_NAME,
                        idle_minutes_to_autostop=spot.
                        SPOT_CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP)

    if handle is None or handle.head_ip is None:
        raise exceptions.ClusterNotUpError('Spot controller is not up.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    code = spot.SpotCodeGen.get_job_table()
    returncode, job_table_payload, stderr = backend.run_on_head(
        handle,
        code,
        require_outputs=True,
        stream_logs=False,
        separate_stderr=True)
    try:
        subprocess_utils.handle_returncode(
            returncode, code, 'Failed to fetch managed job statuses', stderr)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    jobs = spot.load_spot_job_queue(job_table_payload)
    return jobs


# pylint: disable=redefined-builtin
def spot_cancel(name: Optional[str] = None,
                job_ids: Optional[Tuple[int]] = None,
                all: bool = False):
    """Cancel managed spot jobs

    Please refer to the sky.cli.spot_cancel for the document.

    Raises:
        sky.exceptions.ClusterNotUpError: the spot controller is not up.
        RuntimeError: failed to cancel the job.
    """
    job_ids = [] if job_ids is None else job_ids
    _, handle = _is_spot_controller_up(
        'All managed spot jobs should have finished.')
    if handle is None or handle.head_ip is None:
        raise exceptions.ClusterNotUpError('All jobs finished.')

    job_id_str = ','.join(map(str, job_ids))
    if sum([len(job_ids) > 0, name is not None, all]) != 1:
        argument_str = f'job_ids={job_id_str}' if len(job_ids) > 0 else ''
        argument_str += f' name={name}' if name is not None else ''
        argument_str += ' all' if all else ''
        raise ValueError('Can only specify one of JOB_IDS or name or all. '
                         f'Provided {argument_str!r}.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    if all:
        code = spot.SpotCodeGen.cancel_jobs_by_id(None)
    elif job_ids:
        code = spot.SpotCodeGen.cancel_jobs_by_id(job_ids)
    else:
        code = spot.SpotCodeGen.cancel_job_by_name(name)
    # The stderr is redirected to stdout
    returncode, stdout, _ = backend.run_on_head(handle,
                                                code,
                                                require_outputs=True,
                                                stream_logs=False)
    try:
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to cancel managed spot job',
                                           stdout)
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e

    print(stdout)
    if 'Multiple jobs found with name' in stdout:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                'Please specify the job ID instead of the job name.')


def spot_tail_logs(name: Optional[str], job_id: Optional[int], follow: bool):
    """Tail logs of managed spot jobs.

    Please refer to the sky.cli.spot_logs for the document.

    Raises:
        ValueError: invalid arguments.
        sky.exceptions.ClusterNotUpError: the spot controller is not up.
    """
    # TODO(zhwu): Automatically restart the spot controller
    controller_status, handle = _is_spot_controller_up(
        'Please restart the spot controller with '
        f'`sky start {spot.SPOT_CONTROLLER_NAME} -i 5`.')
    if handle is None or handle.head_ip is None:
        msg = 'All jobs finished.'
        if controller_status == global_user_state.ClusterStatus.INIT:
            msg = ''
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(msg)

    if name is not None and job_id is not None:
        raise ValueError('Cannot specify both name and job_id.')
    backend = backend_utils.get_backend_from_handle(handle)
    # Stream the realtime logs
    backend.tail_spot_logs(handle, job_id=job_id, job_name=name, follow=follow)


# ======================
# = Storage Management =
# ======================
def storage_ls() -> List[Dict[str, Any]]:
    """Get the storages.

    Returns:
        [
            {
                'name': str,
                'launched_at': int timestamp of creation,
                'store': List[sky.StoreType],
                'last_use': int timestamp of last use,
                'status': sky.StorageStatus,
            }
        ]
    """
    storages = global_user_state.get_storage()
    for storage in storages:
        storage['store'] = list(storage.pop('handle').sky_stores.keys())
    return storages


def storage_delete(name: Optional[str] = None):
    """Delete a storage.

    Raises:
        ValueError: If the storage does not exist.
    """
    handle = global_user_state.get_handle_from_storage_name(name)
    if handle is None:
        raise ValueError(f'Storage name {name!r} not found.')
    else:
        print(f'Deleting storage object {name!r}.')
        store_object = data.Storage(name=handle.storage_name,
                                    source=handle.source,
                                    sync_on_reconstruction=False)
        store_object.delete()

"""SDK functions for cluster/job management."""
import colorama
import getpass
import json
from typing import List, Optional, Tuple

from sky import backends
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import job_lib
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# ======================
# = Cluster Management =
# ======================


# pylint: disable=redefined-builtin
def status(all: bool, refresh: bool):
    """Get the cluster status in dict.

    Please refer to the sky.cli.status for the document.

    Returns:
        List[dict]:
        [
            {
                'name': (str) cluster name,
                'launched_at': (int) timestamp of launched,
                'last_use': (int) timestamp of last use,
                'status': (global_user_state.ClusterStatus) cluster status,
                'autostop': (int) idle time before autostop,
                'metadata': (dict) metadata of the cluster,
            }
        ]
    """
    cluster_records = backend_utils.get_clusters(all, refresh)
    return cluster_records


# ==================
# = Job Management =
# ==================


def queue(cluster_name: str,
          skip_finished: bool = False,
          all_users: bool = False):
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

    cluster_status, handle = backend_utils.refresh_cluster_status_handle(
        cluster_name)
    backend = backend_utils.get_backend_from_handle(handle)
    if isinstance(backend, backends.LocalDockerBackend):
        # LocalDockerBackend does not support job queues
        raise exceptions.NotSupportedError(
            f'Cluster {cluster_name} with LocalDockerBackend does '
            'not support job queues')
    if cluster_status != global_user_state.ClusterStatus.UP:
        raise exceptions.ClusterNotUpError(
            f'{colorama.Fore.YELLOW}Cluster {cluster_name!r} is not up '
            f'(status: {cluster_status.value}); skipped.'
            f'{colorama.Style.RESET_ALL}')

    logger.info(f'\nSky Job Queue of Cluster {cluster_name}')
    if handle.head_ip is None:
        raise exceptions.ClusterNotUpError(
            f'Cluster {cluster_name} has been stopped or not properly set up. '
            'Please re-launch it with `sky launch` to view the job queue.')

    returncode, job_records, stderr = backend.run_on_head(handle,
                                                          code,
                                                          require_outputs=True)
    if returncode != 0:
        raise RuntimeError(f'{job_records + stderr}\n{colorama.Fore.RED}'
                           f'Failed to get job queue on cluster {cluster_name}.'
                           f'{colorama.Style.RESET_ALL}')
    job_records = json.loads(job_records)
    return job_records


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
        # TODO(zhwu): more exceptions from the backend.
    """
    job_ids = [] if job_ids is None else job_ids
    if len(job_ids) == 0 and not all:
        raise ValueError(
            'sky cancel requires either a job id '
            f'(see `sky queue {cluster_name} -s`) or the --all flag.')

    backend_utils.check_cluster_name_not_reserved(
        cluster_name, operation_str='Cancelling jobs')

    # Check the status of the cluster.
    cluster_status, handle = backend_utils.refresh_cluster_status_handle(
        cluster_name)
    if handle is None:
        raise ValueError(f'Cluster {cluster_name!r} not found'
                         ' (see `sky status`).')
    backend = backend_utils.get_backend_from_handle(handle)
    if not isinstance(backend, backends.CloudVmRayBackend):
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(
                'Job cancelling is only supported for '
                f'{backends.CloudVmRayBackend.NAME}, but cluster '
                f'{cluster_name!r} is created by {backend.NAME}.')
    if cluster_status != global_user_state.ClusterStatus.UP:
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ClusterNotUpError(
                f'{colorama.Fore.YELLOW}Cluster {cluster_name!r} is not up '
                f'(status: {cluster_status.value}); skipped.'
                f'{colorama.Style.RESET_ALL}')

    if all:
        logger.info(f'{colorama.Fore.YELLOW}'
                    f'Cancelling all jobs on cluster {cluster_name!r}...'
                    f'{colorama.Style.RESET_ALL}')
        jobs = None
    else:
        jobs_str = ', '.join(map(str, jobs))
        logger.info(
            f'{colorama.Fore.YELLOW}'
            f'Cancelling jobs ({jobs_str}) on cluster {cluster_name!r}...'
            f'{colorama.Style.RESET_ALL}')

    backend.cancel_jobs(handle, jobs)


# pylint: disable=redefined-builtin
def spot_status(all: bool, refresh: bool):
    """Get statuses of managed spot jobs."""
    raise NotImplementedError()


# pylint: disable=redefined-builtin
def spot_cancel(name: Optional[str], job_ids: Tuple[int], all: bool, yes: bool):
    """Cancel managed spot jobs"""
    raise NotImplementedError()


# ======================
# = Storage Management =
# ======================
def get_storages():
    """List storage objects created."""
    raise NotImplementedError()


def delete_storage(name: str):
    """Delete storage objects."""
    raise NotImplementedError()

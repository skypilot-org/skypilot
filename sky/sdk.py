"""SDK functions for cluster/job management."""
import colorama
import getpass
import json

from sky import backends
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.skylet import job_lib

logger = sky_logging.init_logger(__name__)


# ======================
# = Cluster Management =
# ======================

def status(show_all: bool, refresh: bool):
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
    cluster_records = backend_utils.get_clusters(show_all, refresh)
    return cluster_records


# ==================
# = Job Management =
# ==================


def queue(cluster_name: str, skip_finished: bool=False, all_users: bool=False):
    """Get the job queue in List[dict].

    Please refer to the sky.cli.queue for the document.

    Returns:
        List[dict]:
        [
            {
                'name': (str) job name,
                'cluster': (str) cluster name,
                'status': (global_user_state.JobStatus) job status,
                'metadata': (dict) metadata of the job,
            }
        ]
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
        raise ValueError(f'Cluster {cluster_name} with LocalDockerBackend does '
        'not support job queues')
    if cluster_status != global_user_state.ClusterStatus.UP:
        raise ValueError(f'{colorama.Fore.YELLOW}'
            f'Cluster {cluster_name} is not up (status: {cluster_status.value});'
            ' skipped.'
            f'{colorama.Style.RESET_ALL}')

    logger.info(f'\nSky Job Queue of Cluster {cluster_name}')
    if handle.head_ip is None:
        raise ValueError(
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

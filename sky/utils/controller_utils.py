"""Util constants/functions for SkyPilot Controllers."""
import os
import typing
from typing import Optional

from sky import exceptions
from sky import sky_logging
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# The default idle timeout for skypilot controllers. This include spot
# controller and sky serve controller.
CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 10


# Internal only:
def download_and_stream_latest_job_log(
        backend: 'cloud_vm_ray_backend.CloudVmRayBackend',
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        local_dir: str) -> Optional[str]:
    """Downloads and streams the latest job log.

    This function is only used by spot controller and sky serve controller.
    """
    os.makedirs(local_dir, exist_ok=True)
    log_file = None
    try:
        log_dirs = backend.sync_down_logs(
            handle,
            # Download the log of the latest job.
            # The job_id for the spot job running on the spot cluster is not
            # necessarily 1, as it is possible that the worker node in a
            # multi-node cluster is preempted, and we recover the spot job
            # on the existing cluster, which leads to a larger job_id. Those
            # job_ids all represent the same logical spot job.
            job_ids=None,
            local_dir=local_dir)
    except exceptions.CommandError as e:
        logger.info(f'Failed to download the logs: '
                    f'{common_utils.format_exception(e)}')
    else:
        if not log_dirs:
            logger.error('Failed to find the logs for the user program.')
        else:
            log_dir = list(log_dirs.values())[0]
            log_file = os.path.join(log_dir, 'run.log')

            # Print the logs to the console.
            try:
                with open(log_file) as f:
                    print(f.read())
            except FileNotFoundError:
                logger.error('Failed to find the logs for the user '
                             f'program at {log_file}.')
    return log_file

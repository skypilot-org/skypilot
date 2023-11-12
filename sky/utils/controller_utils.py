"""Util constants/functions for SkyPilot Controllers."""
import dataclasses
import enum
import os
import typing
from typing import Optional

import colorama

from sky import exceptions
from sky import serve as serve_lib
from sky import sky_logging
from sky import spot as spot_lib
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# The default idle timeout for skypilot controllers. This include spot
# controller and sky serve controller.
CONTROLLER_IDLE_MINUTES_TO_AUTOSTOP = 10


@dataclasses.dataclass
class _ControllerSpec:
    """Spec for skypilot controllers."""
    name: str
    cluster_name: str
    in_progress_hint: str
    decline_cancel_hint: str
    decline_down_in_init_status_hint: str
    decline_down_for_dirty_controller_hint: str
    check_cluster_name_hint: str
    default_hint_if_non_existent: str


class Controllers(enum.Enum):
    """Skypilot controllers."""
    # NOTE(dev): Keep this align with
    # sky/cli.py::_CONTROLLER_TO_HINT_OR_RAISE
    SPOT_CONTROLLER = _ControllerSpec(
        name='managed spot controller',
        cluster_name=spot_lib.SPOT_CONTROLLER_NAME,
        in_progress_hint=(
            '* {job_info}To see all spot jobs: '
            f'{colorama.Style.BRIGHT}sky spot queue{colorama.Style.RESET_ALL}'),
        decline_cancel_hint=(
            'Cancelling the spot controller\'s jobs is not allowed.\nTo cancel '
            f'spot jobs, use: {colorama.Style.BRIGHT}sky spot cancel <spot '
            f'job IDs> [--all]{colorama.Style.RESET_ALL}'),
        decline_down_in_init_status_hint=(
            f'{colorama.Fore.RED}Tearing down the spot controller while '
            'it is in INIT state is not supported (this means a spot launch '
            'is in progress or the previous launch failed), as we cannot '
            'guarantee that all the spot jobs are finished. Please wait '
            'until the spot controller is UP or fix it with '
            f'{colorama.Style.BRIGHT}sky start '
            f'{spot_lib.SPOT_CONTROLLER_NAME}{colorama.Style.RESET_ALL}.'),
        decline_down_for_dirty_controller_hint=(
            f'{colorama.Fore.RED}In-progress spot jobs found. To avoid '
            f'resource leakage, cancel all jobs first: {colorama.Style.BRIGHT}'
            f'sky spot cancel -a{colorama.Style.RESET_ALL}\n'),
        check_cluster_name_hint=(
            f'Cluster {spot_lib.SPOT_CONTROLLER_NAME} is reserved for '
            'managed spot controller. '),
        default_hint_if_non_existent='No managed spot jobs are found.')
    SKY_SERVE_CONTROLLER = _ControllerSpec(
        name='sky serve controller',
        cluster_name=serve_lib.SKY_SERVE_CONTROLLER_NAME,
        in_progress_hint=(
            f'* To see detailed service status: {colorama.Style.BRIGHT}'
            f'sky serve status -a{colorama.Style.RESET_ALL}'),
        decline_cancel_hint=(
            'Cancelling the sky serve controller\'s jobs is not allowed.'),
        decline_down_in_init_status_hint=(
            f'{colorama.Fore.RED}Tearing down the sky serve controller '
            'while it is in INIT state is not supported (this means a sky '
            'serve up is in progress or the previous launch failed), as we '
            'cannot guarantee that all the services are terminated. Please '
            'wait until the sky serve controller is UP or fix it with '
            f'{colorama.Style.BRIGHT}sky start '
            f'{serve_lib.SKY_SERVE_CONTROLLER_NAME}'
            f'{colorama.Style.RESET_ALL}.'),
        decline_down_for_dirty_controller_hint=(
            f'{colorama.Fore.RED}Tearing down the sky serve controller is not '
            'supported, as it is currently serving the following services: '
            '{service_names}. Please terminate the services first with '
            f'{colorama.Style.BRIGHT}sky serve down -a'
            f'{colorama.Style.RESET_ALL}.'),
        check_cluster_name_hint=(
            f'Cluster {serve_lib.SKY_SERVE_CONTROLLER_NAME} is reserved for '
            'sky serve controller. '),
        default_hint_if_non_existent='No service is found.')

    @classmethod
    def from_name(cls, name: Optional[str]) -> Optional['Controllers']:
        """Check if the cluster name is a controller name.

        Returns:
            The controller if the cluster name is a controller name.
            Otherwise, returns None.
        """
        for controller in cls:
            if controller.value.cluster_name == name:
                return controller
        return None


def check_cluster_name_not_controller(
        cluster_name: Optional[str],
        operation_str: Optional[str] = None) -> None:
    """Errors out if the cluster name is a controller name.

    Raises:
      sky.exceptions.NotSupportedError: if the cluster name is a controller
        name, raise with an error message explaining 'operation_str' is not
        allowed.

    Returns:
      None, if the cluster name is not a controller name.
    """
    controller = Controllers.from_name(cluster_name)
    if controller is not None:
        msg = controller.value.check_cluster_name_hint
        if operation_str is not None:
            msg += f' {operation_str} is not allowed.'
        with ux_utils.print_exception_no_traceback():
            raise exceptions.NotSupportedError(msg)


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

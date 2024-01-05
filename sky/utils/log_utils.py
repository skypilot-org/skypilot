"""Logging utils."""
import enum
from typing import List, Optional

import colorama
import pendulum
import prettytable

from sky import sky_logging
from sky.utils import rich_utils

logger = sky_logging.init_logger(__name__)


class LineProcessor(object):
    """A processor for log lines."""

    def __enter__(self):
        pass

    def process_line(self, log_line):
        pass

    def __exit__(self, except_type, except_value, traceback):
        del except_type, except_value, traceback  # unused
        pass


class RayUpLineProcessor(LineProcessor):
    """A processor for `ray up` log lines."""

    class ProvisionStatus(enum.Enum):
        LAUNCH = 0
        RUNTIME_SETUP = 1
        PULLING_DOCKER_IMAGES = 2

    def __enter__(self):
        self.state = self.ProvisionStatus.LAUNCH
        self.status_display = rich_utils.safe_status('[bold cyan]Launching')
        self.status_display.start()

    def process_line(self, log_line):
        if ('Success.' in log_line and
                self.state == self.ProvisionStatus.LAUNCH):
            logger.info(f'{colorama.Fore.GREEN}Head node is up.'
                        f'{colorama.Style.RESET_ALL}')
            self.status_display.update(
                '[bold cyan]Launching - Preparing SkyPilot runtime')
            self.state = self.ProvisionStatus.RUNTIME_SETUP
        if ('Pulling from' in log_line and
                self.state == self.ProvisionStatus.RUNTIME_SETUP):
            self.status_display.update(
                '[bold cyan]Launching - Pulling docker images')
            self.state = self.ProvisionStatus.PULLING_DOCKER_IMAGES
        if ('Status: Downloaded newer image' in log_line and
                self.state == self.ProvisionStatus.PULLING_DOCKER_IMAGES):
            logger.info(f'{colorama.Fore.GREEN}Docker image is downloaded.'
                        f'{colorama.Style.RESET_ALL}')
            self.status_display.update(
                '[bold cyan]Launching - Preparing SkyPilot runtime')
            self.state = self.ProvisionStatus.RUNTIME_SETUP

    def __exit__(self, except_type, except_value, traceback):
        del except_type, except_value, traceback  # unused
        self.status_display.stop()


class SkyLocalUpLineProcessor(LineProcessor):
    """A processor for `sky local up` log lines."""

    def __enter__(self):
        status = rich_utils.safe_status('[bold cyan]Creating local cluster - '
                                        'initializing Kubernetes')
        self.status_display = status
        self.status_display.start()

    def process_line(self, log_line):
        if 'Kind cluster created.' in log_line:
            logger.info(f'{colorama.Fore.GREEN}Kubernetes is running.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Installing NVIDIA GPU operator...' in log_line:
            self.status_display.update('[bold cyan]Creating local cluster - '
                                       'Installing NVIDIA GPU operator')
        if 'Starting wait for GPU operator installation...' in log_line:
            self.status_display.update(
                '[bold cyan]Creating local cluster - '
                'waiting for NVIDIA GPU operator installation to complete')
            logger.info('To check NVIDIA GPU operator status, '
                        'see pods: kubectl get pods -n gpu-operator')
        if 'GPU operator installed' in log_line:
            logger.info(f'{colorama.Fore.GREEN}NVIDIA GPU Operator installed.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Pulling SkyPilot GPU image...' in log_line:
            self.status_display.update('[bold cyan]Creating local cluster - '
                                       'pulling and loading SkyPilot GPU image')
        if 'SkyPilot GPU image loaded into kind cluster' in log_line:
            logger.info(f'{colorama.Fore.GREEN}SkyPilot GPU image pulled.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Labelling nodes with GPUs...' in log_line:
            self.status_display.update('[bold cyan]Creating local cluster - '
                                       'launching GPU labelling jobs')
        if ('Starting wait for SkyPilot GPU labeling jobs to complete'
                in log_line):
            self.status_display.update(
                '[bold cyan]Creating local cluster - '
                'waiting for GPU labelling jobs to complete')
            logger.info(
                'To check GPU labelling status, see jobs: '
                'kubectl get jobs -n kube-system -l job=sky-gpu-labeler')
        if 'All SkyPilot GPU labeling jobs completed' in log_line:
            logger.info(f'{colorama.Fore.GREEN}GPU labelling complete.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Pulling SkyPilot CPU image...' in log_line:
            self.status_display.update('[bold cyan]Creating local cluster - '
                                       'pulling and loading SkyPilot CPU image')
        if 'SkyPilot CPU image loaded into kind cluster' in log_line:
            logger.info(f'{colorama.Fore.GREEN}SkyPilot CPU image pulled.'
                        f'{colorama.Style.RESET_ALL}')

    def __exit__(self, except_type, except_value, traceback):
        del except_type, except_value, traceback  # unused
        self.status_display.stop()


def create_table(field_names: List[str], **kwargs) -> prettytable.PrettyTable:
    """Creates table with default style."""
    border = kwargs.pop('border', False)
    align = kwargs.pop('align', 'l')
    table = prettytable.PrettyTable(align=align,
                                    border=border,
                                    field_names=field_names,
                                    **kwargs)
    table.left_padding_width = 0
    table.right_padding_width = 2
    return table


def readable_time_duration(start: Optional[float],
                           end: Optional[float] = None,
                           absolute: bool = False) -> str:
    """Human readable time duration from timestamps.

    Args:
        start: Start timestamp.
        end: End timestamp. If None, current time is used.
        absolute: Whether to return accurate time duration.
    Returns:
        Human readable time duration. e.g. "1 hour ago", "2 minutes ago", etc.
        If absolute is specified, returns the accurate time duration,
          e.g. "1h 2m 23s"
    """
    # start < 0 means that the starting time is not specified yet.
    # It is only used in spot_utils.show_jobs() for job duration calculation.
    if start is None or start < 0:
        return '-'
    if end == start == 0:
        return '-'
    if end is not None:
        end = pendulum.from_timestamp(end)
    start_time = pendulum.from_timestamp(start)
    duration = start_time.diff(end)
    if absolute:
        diff = start_time.diff(end).in_words()
        if duration.in_seconds() < 1:
            diff = '< 1 second'
        diff = diff.replace(' seconds', 's')
        diff = diff.replace(' second', 's')
        diff = diff.replace(' minutes', 'm')
        diff = diff.replace(' minute', 'm')
        diff = diff.replace(' hours', 'h')
        diff = diff.replace(' hour', 'h')
        diff = diff.replace(' days', 'd')
        diff = diff.replace(' day', 'd')
        diff = diff.replace(' weeks', 'w')
        diff = diff.replace(' week', 'w')
        diff = diff.replace(' months', 'mo')
        diff = diff.replace(' month', 'mo')
    else:
        diff = start_time.diff_for_humans(end)
        if duration.in_seconds() < 1:
            diff = '< 1 second'
        diff = diff.replace('second', 'sec')
        diff = diff.replace('minute', 'min')
        diff = diff.replace('hour', 'hr')

    return diff

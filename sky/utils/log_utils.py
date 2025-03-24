"""Logging utils."""
import enum
import time
import types
import typing
from typing import Callable, Iterator, List, Optional, TextIO, Type

import colorama
import prettytable

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.utils import rich_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    # slow due to https://github.com/python-pendulum/pendulum/issues/808
    # FIXME(aylei): bump pendulum if it get fixed
    import pendulum
else:
    pendulum = adaptors_common.LazyImport('pendulum')


class LineProcessor(object):
    """A processor for log lines."""

    def __enter__(self) -> None:
        pass

    def process_line(self, log_line: str) -> None:
        pass

    def __exit__(self, except_type: Optional[Type[BaseException]],
                 except_value: Optional[BaseException],
                 traceback: Optional[types.TracebackType]) -> None:
        del except_type, except_value, traceback  # unused
        pass


class RayUpLineProcessor(LineProcessor):
    """A processor for `ray up` log lines."""

    class ProvisionStatus(enum.Enum):
        LAUNCH = 0
        RUNTIME_SETUP = 1
        PULLING_DOCKER_IMAGES = 2

    def __init__(self, log_path: str):
        self.log_path = log_path

    def __enter__(self) -> None:
        self.state = self.ProvisionStatus.LAUNCH
        self.status_display = rich_utils.safe_status(
            ux_utils.spinner_message('Launching', self.log_path))
        self.status_display.start()

    def process_line(self, log_line: str) -> None:
        if ('Success.' in log_line and
                self.state == self.ProvisionStatus.LAUNCH):
            logger.info('  Head VM is up.')
            self.status_display.update(
                ux_utils.spinner_message(
                    'Launching - Preparing SkyPilot runtime', self.log_path))
            self.state = self.ProvisionStatus.RUNTIME_SETUP
        if ('Pulling from' in log_line and
                self.state == self.ProvisionStatus.RUNTIME_SETUP):
            self.status_display.update(
                ux_utils.spinner_message(
                    'Launching - Initializing docker container', self.log_path))
            self.state = self.ProvisionStatus.PULLING_DOCKER_IMAGES
        if ('Status: Downloaded newer image' in log_line and
                self.state == self.ProvisionStatus.PULLING_DOCKER_IMAGES):
            self.status_display.update(
                ux_utils.spinner_message(
                    'Launching - Preparing SkyPilot runtime', self.log_path))
            self.state = self.ProvisionStatus.RUNTIME_SETUP

    def __exit__(self, except_type: Optional[Type[BaseException]],
                 except_value: Optional[BaseException],
                 traceback: Optional[types.TracebackType]) -> None:
        del except_type, except_value, traceback  # unused
        self.status_display.stop()


class SkyLocalUpLineProcessor(LineProcessor):
    """A processor for `sky local up` log lines."""

    def __init__(self, log_path: str, is_local: bool):
        self.log_path = log_path
        self.is_local = is_local

    def __enter__(self):
        # TODO(romilb): Use ux_utils.INDENT_SYMBOL to be consistent with other
        #  messages.
        msg = 'Creating local cluster - initializing Kubernetes'
        status = rich_utils.safe_status(
            ux_utils.spinner_message(msg,
                                     log_path=self.log_path,
                                     is_local=self.is_local))
        self.status_display = status
        self.status_display.start()

    def process_line(self, log_line: str) -> None:
        if 'Kind cluster created.' in log_line:
            logger.info(f'{colorama.Fore.GREEN}Kubernetes is running.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Installing NVIDIA GPU operator...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'Installing NVIDIA GPU operator',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'Starting wait for GPU operator installation...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'waiting for NVIDIA GPU operator installation to complete',
                    log_path=self.log_path,
                    is_local=self.is_local))
            logger.info('To check NVIDIA GPU operator status, '
                        'see pods: kubectl get pods -n gpu-operator')
        if 'GPU operator installed' in log_line:
            logger.info(f'{colorama.Fore.GREEN}NVIDIA GPU Operator installed.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Pulling SkyPilot GPU image...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'pulling and loading SkyPilot GPU image',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'SkyPilot GPU image loaded into kind cluster' in log_line:
            logger.info(f'{colorama.Fore.GREEN}SkyPilot GPU image pulled.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Labelling nodes with GPUs...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'launching GPU labelling jobs',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if ('Starting wait for SkyPilot GPU labeling jobs to complete'
                in log_line):
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'waiting for GPU labelling jobs to complete',
                    log_path=self.log_path,
                    is_local=self.is_local))
            logger.info(
                'To check GPU labelling status, see jobs: '
                'kubectl get jobs -n kube-system -l job=sky-gpu-labeler')
        if 'All SkyPilot GPU labeling jobs completed' in log_line:
            logger.info(f'{colorama.Fore.GREEN}GPU labelling complete.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Pulling SkyPilot CPU image...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'pulling and loading SkyPilot CPU image',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'SkyPilot CPU image loaded into kind cluster' in log_line:
            logger.info(f'{colorama.Fore.GREEN}SkyPilot CPU image pulled.'
                        f'{colorama.Style.RESET_ALL}')
        if 'Starting installation of Nginx Ingress Controller...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating local cluster - '
                    'creating Nginx Ingress Controller',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'Nginx Ingress Controller installed' in log_line:
            logger.info(
                f'{colorama.Fore.GREEN}Nginx Ingress Controller installed.'
                f'{colorama.Style.RESET_ALL}')
            self.status_display.update(
                ux_utils.spinner_message('Wrapping up local cluster setup',
                                         log_path=self.log_path,
                                         is_local=self.is_local))

    def __exit__(self, except_type: Optional[Type[BaseException]],
                 except_value: Optional[BaseException],
                 traceback: Optional[types.TracebackType]) -> None:
        del except_type, except_value, traceback  # unused
        self.status_display.stop()


class SkyRemoteUpLineProcessor(LineProcessor):
    """A processor for deploy_remote_cluster.sh log lines."""

    def __init__(self, log_path: str, is_local: bool):
        self.log_path = log_path
        self.is_local = is_local

    def __enter__(self) -> None:
        # TODO(romilb): Use ux_utils.INDENT_SYMBOL to be consistent with other
        #  messages.
        status = rich_utils.safe_status(
            ux_utils.spinner_message('Creating remote cluster',
                                     log_path=self.log_path,
                                     is_local=self.is_local))
        self.status_display = status
        self.status_display.start()

    def process_line(self, log_line: str) -> None:
        # Pre-flight checks
        if 'SSH connection successful' in log_line:
            logger.info(f'{colorama.Fore.GREEN}SSH connection established.'
                        f'{colorama.Style.RESET_ALL}')

        # Kubernetes installation steps
        if 'Deploying Kubernetes on head node' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating remote cluster - '
                    'deploying Kubernetes on head node',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'K3s deployed on head node.' in log_line:
            logger.info(f'{colorama.Fore.GREEN}'
                        '✔ K3s successfully deployed on head node.'
                        f'{colorama.Style.RESET_ALL}')

        # Worker nodes
        if 'Deploying Kubernetes on worker node' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating remote cluster - '
                    'deploying Kubernetes on worker nodes',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'Kubernetes deployed on worker node' in log_line:
            logger.info(f'{colorama.Fore.GREEN}'
                        '✔ K3s successfully deployed on worker node.'
                        f'{colorama.Style.RESET_ALL}')

        # Cluster configuration
        if 'Configuring local kubectl to connect to the cluster...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating remote cluster - '
                    'configuring local kubectl',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'kubectl configured to connect to the cluster.' in log_line:
            logger.info(f'{colorama.Fore.GREEN}'
                        '✔ kubectl configured for the remote cluster.'
                        f'{colorama.Style.RESET_ALL}')

        # GPU operator installation
        if 'Installing Nvidia GPU Operator...' in log_line:
            self.status_display.update(
                ux_utils.spinner_message(
                    'Creating remote cluster - '
                    'installing Nvidia GPU Operator',
                    log_path=self.log_path,
                    is_local=self.is_local))
        if 'GPU Operator installed.' in log_line:
            logger.info(f'{colorama.Fore.GREEN}'
                        '✔ Nvidia GPU Operator installed successfully.'
                        f'{colorama.Style.RESET_ALL}')

        # Cleanup steps
        if 'Cleaning up head node' in log_line:
            self.status_display.update(
                ux_utils.spinner_message('Cleaning up head node',
                                         log_path=self.log_path,
                                         is_local=self.is_local))
        if 'Cleaning up node' in log_line:
            self.status_display.update(
                ux_utils.spinner_message('Cleaning up worker node',
                                         log_path=self.log_path,
                                         is_local=self.is_local))
        if 'cleaned up successfully' in log_line:
            logger.info(f'{colorama.Fore.GREEN}'
                        f'{log_line.strip()}{colorama.Style.RESET_ALL}')

        # Final status
        if 'Cluster deployment completed.' in log_line:
            logger.info(f'{colorama.Fore.GREEN}✔ Remote k3s is running.'
                        f'{colorama.Style.RESET_ALL}')

    def __exit__(self, except_type: Optional[Type[BaseException]],
                 except_value: Optional[BaseException],
                 traceback: Optional[types.TracebackType]) -> None:
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
    # It is only used in jobs_utils.format_job_table() for job duration
    # calculation.
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


def follow_logs(
    file: TextIO,
    *,
    should_stop: Callable[[], bool],
    stop_on_eof: bool = False,
    process_line: Optional[Callable[[str], Iterator[str]]] = None,
    idle_timeout_seconds: Optional[int] = None,
) -> Iterator[str]:
    """Streams and processes logs line by line from a file.

    Args:
        file: File object to read logs from.
        should_stop: Callback that returns True when streaming should stop.
        stop_on_eof: If True, stop when reaching end of file.
        process_line: Optional callback to transform/filter each line.
        idle_timeout_seconds: If set, stop after these many seconds without
            new content.

    Yields:
        Log lines, possibly transformed by process_line if provided.
    """
    current_line: str = ''
    seconds_without_content: int = 0

    while True:
        content = file.readline()

        if not content:
            if stop_on_eof or should_stop():
                break

            if idle_timeout_seconds is not None:
                if seconds_without_content >= idle_timeout_seconds:
                    break
                seconds_without_content += 1

            time.sleep(1)
            continue

        seconds_without_content = 0
        current_line += content

        if '\n' in current_line or '\r' in current_line:
            if process_line is not None:
                yield from process_line(current_line)
            else:
                yield current_line
            current_line = ''

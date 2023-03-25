"""Logging utils."""
import enum
import threading
import re
from typing import List, Optional

import rich.console as rich_console
from rich.progress import Progress

import colorama
import pendulum
import prettytable

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

console = rich_console.Console()
_status = None


class _NoOpConsoleStatus:
    """An empty class for multi-threaded console.status."""

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def update(self, text):
        pass

    def stop(self):
        pass

    def start(self):
        pass


def safe_rich_status(msg: str):
    """A wrapper for multi-threaded console.status."""
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        global _status
        if _status is None:
            _status = console.status(msg)
        _status.update(msg)
        return _status
    return _NoOpConsoleStatus()


def force_update_rich_status(msg: str):
    """Update the status message even if sky_logging.is_silent() is true."""
    if threading.current_thread() is threading.main_thread():
        global _status
        if _status is not None:
            _status.update(msg)


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

    def __enter__(self):
        self.state = self.ProvisionStatus.LAUNCH
        self.status_display = safe_rich_status('[bold cyan]Launching')
        self.status_display.start()

    def process_line(self, log_line):
        if ('Shared connection to' in log_line and
                self.state == self.ProvisionStatus.LAUNCH):
            self.status_display.stop()
            logger.info(f'{colorama.Fore.GREEN}Head node is up.'
                        f'{colorama.Style.RESET_ALL}')
            self.status_display.start()
            self.status_display.update(
                '[bold cyan]Launching - Preparing SkyPilot runtime')
            self.state = self.ProvisionStatus.RUNTIME_SETUP

    def __exit__(self, except_type, except_value, traceback):
        del except_type, except_value, traceback  # unused
        self.status_display.stop()

class ProgressSample():
    """Sample of progress for a given time."""

    timestamp: float
    """Timestamp of sample."""
    completed: float
    """Number of steps completed."""
    
class RsyncLineBarProcessor(Progress):
    """A processor for `rsync` log lines."""
    # original Progress class defined inttps://g
    # https://github.com/Textualize/rich/blob/master/rich/progress.py
    class RsyncStatus(enum.Enum):
        STARTLOG = 0
        RUNTIME_SETUP = 1

    def __init__(
        self,        
        transient: bool = False,
        redirect_stdout: bool = True,
        redirect_stderr: bool = True
    ):
        self.progress_bar_track_ids = []
        self.prev_percentage = 0
        super().__init__(transient=transient, redirect_stdout=redirect_stdout, redirect_stderr=redirect_stderr)

    def __enter__(self):
        self.state = None
        self.status_display = safe_rich_status('[bold cyan]Syncing')
        self.status_display.start()


    def update(
        self,
        task_id: int,
        line: str
    ) -> None:
        """Update information associated with a task.

        Args:
            task_id (TaskID): Task id (returned by add_task).
            total (float, optional): Updates task.total if not None.
            completed (float, optional): Updates task.completed if not None.
            advance (float, optional): Add a value to task.completed if not None.
            description (str, optional): Change task description if not None.
            visible (bool, optional): Set visible flag if not None.
            refresh (bool): Force a refresh of progress information. Default is False.
            **fields (Any): Additional data fields required for rendering.
        """
        with self._lock:
            task = self._tasks[task_id]
            completed_start = task.completed
            pattern = r'(\d+)%'
            result = re.search(pattern, line)
            if result:
                percentage = int(result.group(1))

                advance = self.prev_percentage - percentage 
                self.prev_percentage = percentage
                task.completed += advance
                update_completed = task.completed - completed_start

                current_time = self.get_time()
                old_sample_time = current_time - self.speed_estimate_period
                _progress = task._progress

                popleft = _progress.popleft
                while _progress and _progress[0].timestamp < old_sample_time:
                    popleft()
                if update_completed > 0:
                    _progress.append(ProgressSample(current_time, update_completed))
                if (
                    task.total is not None
                    and task.completed >= task.total
                    and task.finished_time is None
                ):
                    task.finished_time = task.elapsed
            else:
                return


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
    else:
        diff = start_time.diff_for_humans(end)
        if duration.in_seconds() < 1:
            diff = '< 1 second'
        diff = diff.replace('second', 'sec')
        diff = diff.replace('minute', 'min')
        diff = diff.replace('hour', 'hr')

    return diff

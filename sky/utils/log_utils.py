"""Logging utils."""
import enum
import threading
import re
from typing import List, Optional, NamedTuple, Any, NewType, Dict

import rich.console as rich_console
from rich.progress import Progress, Task

import colorama
import pendulum
import prettytable

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

console = rich_console.Console()
_status = None
TaskID = NewType('TaskID', int)


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


def safe_rich_progress_bar():
    """A wrapper for multi-threaded console.status."""
    if (threading.current_thread() is threading.main_thread() and
            not sky_logging.is_silent()):
        global _status
        if _status is None:
            _status = RsyncProgressBarProcessor()
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


class ProgressSample(NamedTuple):
    """Sample of progress for a given time."""

    timestamp: float
    """Timestamp of sample."""
    completed: float
    """Number of steps completed."""


class RsyncProgressBarProcessor(LineProcessor, Progress):
    """A progress bar processor for `rsync` log lines."""

    # original Progress class defined in
    # https://github.com/Textualize/rich/blob/master/rich/progress.py
    class Status(enum.Enum):
        LOG = 0
        RUNTIME_SETUP = 1

    def __init__(self,
                 transient: bool = True,
                 redirect_stdout: bool = True,
                 redirect_stderr: bool = True):
        self.current_task_id = None
        self._tasks: Dict[TaskID, Task] = {}
        self.state = None
        self._task_index = 1
        super().__init__(transient=transient,
                         redirect_stdout=redirect_stdout,
                         redirect_stderr=redirect_stderr)

    def __enter__(self):
        self.start()
        return self
        

    def get_current_task_id(self):
        """returns the task_id currently being processed"""
        if self.current_task_id:
            return self.current_task_id
        return None

    def add_task(
        self,
        description: str,
        start: bool = True,
        total: Optional[float] = 100.0,
        completed: int = 0,
        visible: bool = True,
        **fields: Any,
    ) -> TaskID:
        """Add a new 'task' to the Progress display.

        Args:
            description: A description of the task.
            start: Start the task immediately. If set to False,
                you will need to call `start` manually. Defaults to True.
            total: Number of total steps in the progress if known.
                Set to None to render a pulsing animation. Defaults to 100.
            completed: Number of steps completed so far. Defaults to 0.
            visible: Enable display of the task. Defaults to True.
            **fields: Additional data fields required for rendering.

        Returns:
            TaskID: An ID you can use when calling `update`.
        """
        with self._lock:
            task = Task(
                self._task_index,
                description,
                total,
                completed,
                visible=visible,
                fields=fields,
                _get_time=self.get_time,
                _lock=self._lock,
            )
            self._tasks[self._task_index] = task
            if start:
                self.start_task(self._task_index)
            new_task_index = self._task_index
            self.current_task_id = new_task_index
            self._task_index = TaskID(int(self._task_index) + 1)
        self.refresh()
        return new_task_index

    def update(
        self,
        task_id: TaskID,
        line: str,
        refresh: bool = False,
    ) -> None:
        """Process the string read from stdout of rsync
           command and update information associated with a task.

        Args:
            task_id: Task id (returned by add_task).
            line: string read from output of the rsync command
            refresh: Force a refresh of progress information.
        """

        with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                completed_start = task.completed
                pattern = r'(\d+)%'
                result = re.search(pattern, line)
                if result:
                    percentage = int(result.group(1))
                    task.completed = percentage
                    update_completed = task.completed - completed_start
                    current_time = self.get_time()
                    old_sample_time = current_time - self.speed_estimate_period
                    progress = task._progress
                    popleft = progress.popleft
                    while (progress and
                           progress[0].timestamp < old_sample_time):
                        popleft()
                    if update_completed > 0:
                        progress.append(
                            ProgressSample(current_time, update_completed))
                    if (task.total is not None and
                            task.completed >= task.total and
                            task.finished_time is None):
                        task.finished_time = task.elapsed

        if refresh:
            self.refresh()

    def remove_task_if_complete(self, task_id: TaskID) -> None:
        """Delete a task if it exists.

        Args:
            task_id (TaskID): A task ID.
        """
        with self._lock:
            if task_id in self._tasks and self._tasks[task_id].completed == 100:
                del self._tasks[task_id]

    def __exit__(self, except_type, except_value, traceback):
        del except_type, except_value, traceback  # unused
        self.stop()


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

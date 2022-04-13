"""Sky logging utils."""
import enum
from typing import Optional

import colorama
import pendulum
import prettytable
import rich.status

from sky import sky_logging

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

    def __enter__(self):
        self.state = self.ProvisionStatus.LAUNCH
        self.status_display = rich.status.Status('[bold cyan]Launching')
        self.status_display.start()

    def process_line(self, log_line):
        if ('Shared connection to' in log_line and self.state == self.ProvisionStatus.LAUNCH):
            self.status_display.stop()
            logger.info(f'{colorama.Fore.GREEN}Head node is up.'
                        f'{colorama.Style.RESET_ALL}')
            self.status_display.start()
            self.status_display.update('[bold cyan]Launching - Preparing Sky runtime')
            self.state = self.ProvisionStatus.RUNTIME_SETUP

    def __exit__(self, except_type, except_value, traceback):
        del except_type, except_value, traceback  # unused
        self.status_display.stop()


def create_table(field_names):
    """Creates table with default style."""
    table = prettytable.PrettyTable()
    table.field_names = field_names
    table.border = False
    table.left_padding_width = 0
    table.right_padding_width = 2
    table.align = 'l'
    return table


def readable_time_duration(start: Optional[int],
                           end: Optional[int] = None,
                           absolute: bool = False) -> str:
    """Human readable time duration from timestamps."""
    if start is None:
        return '-'
    if end is not None:
        end = pendulum.from_timestamp(end)
    duration = pendulum.from_timestamp(start)
    diff = duration.diff_for_humans(end, absolute=absolute)
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')
    return diff

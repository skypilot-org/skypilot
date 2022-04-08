"""Utilities shared by skylet and sky."""
from typing import Optional

import prettytable
import pendulum


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

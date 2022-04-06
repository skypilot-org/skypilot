"""Utilities shared by CLI functions."""
import pendulum
import time


def readable_time_duration(start_time: int):
    duration = pendulum.now().subtract(seconds=time.time() - start_time)
    diff = duration.diff_for_humans()
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')
    return diff


def truncate_long_string(s: str, max_length: int = 35) -> str:
    if len(s) <= max_length:
        return s
    splits = s.split(' ')
    if len(splits[0]) > max_length:
        return splits[0][:max_length] + '...'  # Use '…'?
    # Truncate on word boundary.
    i = 0
    total = 0
    for i, part in enumerate(splits):
        total += len(part)
        if total >= max_length:
            break
    return ' '.join(splits[:i]) + ' ...'

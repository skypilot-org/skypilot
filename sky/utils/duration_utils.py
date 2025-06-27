"""Utils for duration."""
import time


def human_duration_since(unix_timestamp: int) -> str:
    """Calculates the time elapsed since a given Unix timestamp and returns
       it as a human-readable string, similar to Kubernetes' duration format.

    Args:
        unix_timestamp: The start time as a Unix timestamp (seconds
        since epoch).

    Returns:
        A string representing the duration, e.g., "2d3h", "15m", "30s".
        Returns "0s" for zero, negative durations, or if the timestamp
        is invalid.
    """
    if not unix_timestamp or unix_timestamp <= 0:
        return '0s'

    try:
        start_time = int(unix_timestamp)
    except (ValueError, TypeError):
        return '0s'

    now = int(time.time())
    duration_seconds = now - start_time

    units = {
        'y': 365 * 24 * 60 * 60,
        'd': 60 * 60 * 24,
        'h': 60 * 60,
        'm': 60,
        's': 1,
    }

    if duration_seconds <= 0:
        return '0s'
    elif duration_seconds < 60 * 2:
        return f'{duration_seconds}s'

    minutes = int(duration_seconds / units['m'])
    if minutes < 10:
        s = int(duration_seconds / units['s']) % 60
        if s == 0:
            return f'{minutes}m'
        return f'{minutes}m{s}s'
    elif minutes < 60 * 3:
        return f'{minutes}m'

    hours = int(duration_seconds / units['h'])
    days = int(hours / 24)
    years = int(hours / 24 / 365)
    if hours < 8:
        m = int(duration_seconds / units['m']) % 60
        if m == 0:
            return f'{hours}h'
        return f'{hours}h{m}m'
    elif hours < 48:
        return f'{hours}h'
    elif hours < 24 * 8:
        h = hours % 24
        if h == 0:
            return f'{days}d'
        return f'{days}d{h}h'
    elif hours < 24 * 365 * 2:
        return f'{days}d'
    elif hours < 24 * 365 * 8:
        dy = int(hours / 24) % 365
        if dy == 0:
            return f'{years}y'
        return f'{years}y{dy}d'
    return f'{years}y'

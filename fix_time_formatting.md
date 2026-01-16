# Fix: Time Formatting in `sky jobs queue` CLI

## Issue

The `sky jobs queue` CLI was displaying imprecise/rounded times in the SUBMITTED column (e.g., "1 hr ago" for jobs submitted 50 minutes ago), while the frontend dashboard showed precise times (e.g., "50m ago").

## Screenshots Reference

- Frontend: `/mnt/c/Users/Administrator/Downloads/aa4e0ae7-b0e9-415a-ab37-ce4d87f23390.png`
- Backend CLI: `/mnt/c/Users/Administrator/Downloads/db8cfc7a-5409-4ce5-b44a-69e6fd0ebf28.png`

## Root Cause

The backend CLI used `pendulum.diff_for_humans()` in `sky/utils/log_utils.py:readable_time_duration()` which rounds aggressively:
- 45+ minutes → "about 1 hour ago"
- This made the CLI output inconsistent with the frontend dashboard

## Solution

Modified `readable_time_duration()` to use custom precise time formatting instead of pendulum's aggressive rounding.

## Files Changed

### 1. `sky/utils/log_utils.py` (lines 255-315)

**Before:**
```python
else:
    diff = start_time.diff_for_humans(end)
    if duration.in_seconds() < 1:
        diff = '< 1 second'
    diff = diff.replace('second', 'sec')
    diff = diff.replace('minute', 'min')
    diff = diff.replace('hour', 'hr')

return diff
```

**After:**
```python
else:
    # Use precise time formatting instead of pendulum's diff_for_humans()
    # which rounds aggressively (e.g., 50 minutes becomes "about 1 hour").
    total_seconds = duration.in_seconds()
    # Determine suffix: "ago" if start is in the past (relative to end),
    # "before" if start is before the explicitly provided end time
    is_past = total_seconds < 0  # start is after end (unusual case)
    total_seconds = abs(total_seconds)

    # For very long durations (1 week+), use pendulum for readability
    if total_seconds >= 604800:
        diff = start_time.diff_for_humans(end)
        diff = diff.replace('second', 'sec')
        diff = diff.replace('minute', 'min')
        diff = diff.replace('hour', 'hr')
        return diff

    # Determine the suffix based on direction
    # If end was explicitly provided and start < end, use "before"
    # If end defaults to now and start is in the past, use "ago"
    if is_past:
        suffix = 'from now'
    elif end_was_none:
        # end defaults to now, so this is a "time ago" situation
        suffix = 'ago'
    else:
        # end was explicitly provided
        suffix = 'before'

    if total_seconds < 1:
        diff = '< 1 sec'
    elif total_seconds < 60:
        # Less than 1 minute: show seconds
        secs = int(total_seconds)
        if secs == 1:
            diff = f'1 sec {suffix}'
        else:
            diff = f'{secs} secs {suffix}'
    elif total_seconds < 3600:
        # Less than 1 hour: show precise minutes
        minutes = int(total_seconds // 60)
        if minutes == 1:
            diff = f'1 min {suffix}'
        else:
            diff = f'{minutes} mins {suffix}'
    elif total_seconds < 86400:
        # Less than 1 day: show hours
        hours = int(total_seconds // 3600)
        if hours == 1:
            diff = f'1 hr {suffix}'
        else:
            diff = f'{hours} hrs {suffix}'
    else:
        # Less than 1 week: show days
        days = int(total_seconds // 86400)
        if days == 1:
            diff = f'1 day {suffix}'
        else:
            diff = f'{days} days {suffix}'

return diff
```

Also added tracking for whether `end` was originally None (line 236):
```python
# Track whether end was explicitly provided (for suffix determination later)
end_was_none = end is None
```

### 2. `tests/unit_tests/test_sky/utils/test_log_utils.py` (line 40)

**Before:**
```python
(NOW, NOW + 10, 'a few secs before', '10s'),
```

**After:**
```python
(NOW, NOW + 10, '10 secs before', '10s'),
```

## Output Comparison

| Duration | Before (pendulum) | After (precise) |
|----------|-------------------|-----------------|
| 30 sec   | "a few secs ago"  | "30 secs ago"   |
| 7 min    | "7 mins ago"      | "7 mins ago"    |
| 45 min   | "about 1 hr ago"  | "45 mins ago"   |
| 50 min   | "about 1 hr ago"  | "50 mins ago"   |
| 1.5 hr   | "about 2 hrs ago" | "1 hr ago"      |
| 3 hr     | "3 hrs ago"       | "3 hrs ago"     |
| 1 day    | "1 day ago"       | "1 day ago"     |

## Testing

```bash
# Run unit tests
python -m pytest tests/unit_tests/test_sky/utils/test_log_utils.py -v

# All 20 tests pass
```

## To Apply Fix

```bash
# Restart the API server to pick up the code changes
sky api stop && sky api start

# Check job times in CLI
sky jobs queue
```

## Status

- [x] Fix implemented
- [x] Unit tests updated and passing
- [x] Code formatted with `format.sh`
- [ ] Manual testing with real jobs (pending API restart)

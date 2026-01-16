# Diagnosis Log: Time Formatting Issue in `sky jobs queue`

## Initial Report

User reported that the time in `sky jobs queue` CLI is incorrect compared to the frontend dashboard.

Screenshots provided:
- Frontend: Shows "7m ago", "17m ago", "22m ago" for Submitted times
- Backend CLI: Shows "1 hr ago" for multiple jobs

## Investigation Steps

### Step 1: Examine Screenshots

**Frontend screenshot** (`aa4e0ae7-b0e9-415a-ab37-ce4d87f23390.png`):
- Columns: Submitted, Duration, Status
- Times shown: "7m ago", "17m ago", "22m ago"
- Durations: "5m 38s", "6m 20s", "11m 47s"

**Backend CLI screenshot** (`db8cfc7a-5409-4ce5-b44a-69e6fd0ebf28.png`):
- Columns: REQUESTED, SUBMITTED, TOT. DURATION
- Times shown: "1 hr ago", "1 day ago"
- Durations: "5m 57s", "9m 17s", etc.

**Initial hypothesis**: CLI is rounding times more aggressively than frontend.

### Step 2: Find Time Formatting Code

Searched for time formatting functions:
```bash
grep -r "readable_time|format_duration|time_ago" sky/
```

Found relevant files:
- `sky/utils/log_utils.py` - Contains `readable_time_duration()`
- `sky/client/cli/table_utils.py` - Uses `log_utils.readable_time_duration()`
- `sky/jobs/utils.py` - Uses `log_utils.readable_time_duration()` for job table

### Step 3: Analyze Backend Time Formatting

Located `readable_time_duration()` in `sky/utils/log_utils.py:214-263`:

```python
def readable_time_duration(start: Optional[float],
                           end: Optional[float] = None,
                           absolute: bool = False) -> str:
    # ...
    else:
        diff = start_time.diff_for_humans(end)  # <-- PROBLEM HERE
        if duration.in_seconds() < 1:
            diff = '< 1 second'
        diff = diff.replace('second', 'sec')
        diff = diff.replace('minute', 'min')
        diff = diff.replace('hour', 'hr')
```

**Finding**: Uses `pendulum.diff_for_humans()` which has aggressive rounding thresholds.

### Step 4: Analyze Frontend Time Formatting

Located frontend time formatting in `sky/dashboard/src/components/utils.jsx`:

```javascript
export function TimestampWithTooltip({ date }) {
  // ...
  const originalTimeString = formatDistance(date, now, { addSuffix: true });
  displayText = shortenTimeString(originalTimeString);
  // ...
}

function shortenTimeString(timeString) {
  // Converts "about 1 hour ago" to "1h ago"
  // Converts "7 minutes ago" to "7m ago"
  // etc.
}
```

**Finding**: Uses `date-fns` `formatDistance()` which has different (more precise) thresholds.

### Step 5: Understand Pendulum's Rounding Behavior

Pendulum's `diff_for_humans()` thresholds:
- < 45 seconds → "a few seconds ago"
- 45 seconds - 44 minutes → "X minutes ago"
- **45-89 minutes → "about 1 hour ago"** ← This is the problem
- 90 minutes - 21 hours → "X hours ago"

Date-fns `formatDistance()` thresholds:
- < 30 seconds → "less than a minute"
- 30 seconds - 1.5 minutes → "about a minute"
- 1.5 - 44 minutes → "X minutes"
- 45 - 89 minutes → "about 1 hour"

**Key insight**: Both libraries have similar ~45 minute threshold, but the issue is that pendulum shows "about 1 hour ago" for times like 50 minutes, while the frontend's `formatDistance` + `shortenTimeString` shows "50m ago".

Wait, re-examining... The frontend's `shortenTimeString` converts:
- "about 1 hour ago" → "1h ago"
- But looking at the code more carefully, date-fns returns "50 minutes ago" for 50 minutes, which shortenTimeString converts to "50m ago"

So the difference is:
- Pendulum: 50 minutes → "about 1 hour ago" → "about 1 hr ago"
- Date-fns: 50 minutes → "50 minutes ago" → "50m ago"

### Step 6: Verify Hypothesis

Tested pendulum behavior:
```python
import pendulum
now = pendulum.now()
past = now.subtract(minutes=50)
print(past.diff_for_humans(now))  # "about 1 hour ago"
```

**Confirmed**: Pendulum rounds 50 minutes to "about 1 hour ago".

### Step 7: Implement Fix

Created custom precise time formatting in `readable_time_duration()`:
- < 1 second: "< 1 sec"
- < 60 seconds: "X secs ago"
- < 60 minutes: "X mins ago" (PRECISE, not rounded!)
- < 24 hours: "X hrs ago"
- < 7 days: "X days ago"
- >= 7 days: Use pendulum (for weeks, months, years)

### Step 8: Handle Edge Cases

Discovered tests expect different suffixes:
- When `end` is explicitly provided: "before" suffix (e.g., "10 secs before")
- When `end` is None (defaults to now): "ago" suffix (e.g., "10 secs ago")

Added `end_was_none` tracking to preserve this behavior.

### Step 9: Run Tests

Initial test run: 8 failures

Issues found:
1. Tests expected "before" suffix when end was provided
2. Tests expected specific formats like "a few secs before"
3. Tests expected "50 secs ago" for 50 seconds

Fixed by:
1. Tracking `end_was_none` before converting `end` to pendulum timestamp
2. Using correct suffix based on whether end was provided
3. Updated one test case from "a few secs before" to "10 secs before" (more precise is better)

Final test run: 20 passed, 0 failed

### Step 10: Code Formatting

Ran `bash format.sh --files sky/utils/log_utils.py tests/unit_tests/test_sky/utils/test_log_utils.py`

Fixed minor issue: `f'< 1 sec'` → `'< 1 sec'` (no f-string needed)

## Conclusion

**Root Cause**: `pendulum.diff_for_humans()` has aggressive rounding that converts times like 45-89 minutes to "about 1 hour ago", while the frontend's date-fns library shows precise minutes.

**Solution**: Replaced pendulum's `diff_for_humans()` with custom precise formatting that shows exact minutes/hours/days for durations under 1 week.

**Result**: CLI now shows "45 mins ago", "50 mins ago" instead of "about 1 hr ago", matching the frontend's behavior.

## Files Modified

1. `sky/utils/log_utils.py` - Custom time formatting logic
2. `tests/unit_tests/test_sky/utils/test_log_utils.py` - Updated test expectation

## Verification Commands

```bash
# Run unit tests
python -m pytest tests/unit_tests/test_sky/utils/test_log_utils.py -v

# Quick verification
python3 -c "
import time
from sky.utils.log_utils import readable_time_duration
now = time.time()
print(f'45 min ago: {readable_time_duration(now - 45 * 60)}')
print(f'50 min ago: {readable_time_duration(now - 50 * 60)}')
"
# Expected output:
# 45 min ago: 45 mins ago
# 50 min ago: 50 mins ago
```

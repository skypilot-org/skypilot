"""Optional absolute Sandbox lifetime, independent of SSH/job readiness."""

import math
import time
from typing import List, Optional

TAG = 'skypilot-deadline'

# The image always includes Python. Ending its entrypoint ends the Sandbox,
# including setup, jobs and SSH sessions. Do not start a fresh relative timer
# after Modal schedules a delayed container.
_SUPERVISOR = """\
import os, signal, subprocess, sys, time

def cancel(signum, frame):
    raise SystemExit(128 + signum)

signal.signal(signal.SIGTERM, cancel)
signal.signal(signal.SIGINT, cancel)
deadline = float(sys.argv[1])
if deadline <= time.time():
    raise SystemExit(124)
child = subprocess.Popen(['bash', '-lc', sys.argv[2]], start_new_session=True)
try:
    try:
        result = child.wait(timeout=max(0, deadline - time.time()))
    except subprocess.TimeoutExpired:
        result = 124
finally:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
raise SystemExit(result)
"""


def validate(value: Optional[float]) -> Optional[float]:
    if value is not None and (type(value) not in (int, float) or
                              not math.isfinite(value) or value <= 0):
        raise ValueError(
            'modal.deadline must be a positive finite Unix timestamp')
    return float(value) if value is not None else None


def remaining(value: float) -> int:
    seconds = math.floor(value - time.time())
    if seconds < 1:
        raise TimeoutError('Modal Sandbox deadline has passed')
    return seconds


def command(shell: str, value: Optional[float]) -> List[str]:
    if value is None:
        return ['bash', '-lc', shell]
    return ['python3', '-c', _SUPERVISOR, str(value), shell]

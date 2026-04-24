"""Lifecycle-hook executor on each cluster node.

Runs `resources.hooks` entries in response to teardown events
(`autostop`, `preemption`, `down`). Head and worker processes both
use the same `run(event, hooks)` function — the only difference is
the trigger ingress.

Concurrency: each node has its own file-lock on
``~/.sky/hooks/.teardown_claim``; `try_claim_teardown` is atomic
across every process that can touch the node's filesystem. The
guarantee is per-node (see termination_hook_design.md §1.6.5).
"""
import fcntl
import os
import subprocess
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.skylet import constants
from sky.skylet import log_lib

logger = sky_logging.init_logger(__name__)

# Known events. Kept in sync with sky.utils.schemas._HOOK_EVENTS.
AUTOSTOP = 'autostop'
PREEMPTION = 'preemption'
DOWN = 'down'
EVENTS = (AUTOSTOP, PREEMPTION, DOWN)

# Where per-event log files + the file-lock marker live on each node.
HOOK_LOG_DIR = os.path.expanduser('~/.sky/hooks')
CLAIM_FILE = os.path.join(HOOK_LOG_DIR, '.teardown_claim')


def _ensure_dir() -> None:
    os.makedirs(HOOK_LOG_DIR, exist_ok=True)


def try_claim_teardown(event: str) -> bool:
    """Atomically claim the teardown slot on this node.

    First caller wins; subsequent callers see the claim and return
    False. The lock is held only during the check+write; it is not
    held during hook execution.

    Returns True if this call set the claim; False if it was already set.
    """
    _ensure_dir()
    fd = os.open(CLAIM_FILE, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = os.read(fd, 64).decode().strip()
        if current:
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, event.encode())
        return True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def current_teardown_event() -> Optional[str]:
    """Return the claimed teardown event on this node, or None."""
    try:
        with open(CLAIM_FILE, 'r', encoding='utf-8') as f:
            value = f.read().strip()
            return value or None
    except FileNotFoundError:
        return None


def clear_teardown_claim() -> None:
    """Unlink the claim file. Called by skylet boot / handler boot /
    provision setup so a prior crashed process cannot block a fresh
    cluster's hooks."""
    try:
        os.unlink(CLAIM_FILE)
    except FileNotFoundError:
        pass


def reset_teardown_state_for_tests() -> None:
    """Test-only alias for `clear_teardown_claim()`."""
    clear_teardown_claim()


def _log_path_for(event: str) -> str:
    return os.path.join(HOOK_LOG_DIR, f'{event}.log')


def _run_script(script: str, log_path: str, timeout: int) -> int:
    """Execute a single hook script, teeing output to `log_path`.

    Returns the process exit code. Timeouts and other failures are
    converted into a non-zero return so the caller can keep going.
    """
    try:
        return log_lib.run_with_log(script,
                                    log_path,
                                    require_outputs=False,
                                    shell=True,
                                    process_stream=True,
                                    timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f'Hook timed out after {timeout}s; continuing teardown.')
        return 124
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Hook execution failed: {e}; continuing teardown.')
        return 1


def run(event: str, hooks: Optional[List[Dict[str, Any]]]) -> None:
    """Run every hook whose `events` list contains `event`, in order.

    The per-event log file ``~/.sky/hooks/<event>.log`` is truncated
    before the first matching hook runs and appended for subsequent
    matches. Non-zero exit codes and timeouts are logged as warnings;
    teardown continues.
    """
    if not hooks:
        return
    if event not in EVENTS:
        logger.warning(f'Ignoring unknown hook event: {event!r}.')
        return

    matching = [h for h in hooks if event in (h.get('events') or [])]
    if not matching:
        return

    _ensure_dir()
    log_path = _log_path_for(event)
    try:
        with open(log_path, 'w', encoding='utf-8'):
            pass  # truncate
    except OSError as e:
        logger.warning(f'Could not truncate hook log {log_path}: {e}')

    for idx, entry in enumerate(matching):
        script = entry['run']
        timeout = entry.get('timeout',
                            constants.DEFAULT_AUTOSTOP_HOOK_TIMEOUT_SECONDS)
        logger.info(f'Running {event} hook {idx + 1}/{len(matching)} '
                    f'(timeout={timeout}s).')
        rc = _run_script(script, log_path, timeout)
        if rc != 0:
            logger.warning(
                f'{event} hook exited with code {rc}; continuing teardown.')

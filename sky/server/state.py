"""State for API server process."""
import threading
from typing import Callable, List

# This state is used to block requests except /api operations, which is useful
# when a server is shutting down: new requests will be blocked, but existing
# requests will be allowed to finish and be operated via /api operations, e.g.
# /api/logs, /api/cancel, etc.
_block_requests = False

# Process-local "shutdown initiated" flag. Set by OSS signal handlers
# (Server.handle_exit for single-worker / main, SlowStartMultiprocess.
# handle_term/handle_int for the multi-worker supervisor) and read by
# any in-process code that needs to short-circuit on shutdown (e.g.
# plugin-provided readiness endpoints in the supervisor).
#
# Kept in memory rather than a sentinel file so a hard crash mid-
# shutdown doesn't leave the next pod stuck NotReady forever. Plugins
# that need to react do so by registering a callback via
# `register_shutdown_callback`, not by polling — this keeps the
# dependency direction clean (OSS notifies, plugin owns its own
# state).
_shutting_down = False
_shutdown_callbacks: List[Callable[[], None]] = []
_shutdown_lock = threading.Lock()


# TODO(aylei): refactor, state should be a instance property of API server app
# instead of a global variable.
def get_block_requests() -> bool:
    """Whether block requests except /api operations."""
    return _block_requests


def set_block_requests(shutting_down: bool) -> None:
    """Set the API server to block requests except /api operations."""
    global _block_requests
    _block_requests = shutting_down


def set_shutting_down() -> None:
    """Mark this process as shutting down and fire registered callbacks.

    Idempotent: subsequent calls are a no-op so a process that sees
    SIGTERM via multiple paths (e.g. uvicorn supervisor + our own
    handler) doesn't fire callbacks twice.
    """
    global _shutting_down
    with _shutdown_lock:
        if _shutting_down:
            return
        _shutting_down = True
        callbacks = list(_shutdown_callbacks)
    for fn in callbacks:
        try:
            fn()
        except Exception:  # pylint: disable=broad-except
            # A misbehaving callback must not block the shutdown path —
            # we're already on a signal-handling code path with no good
            # way to surface the error. Swallow and continue.
            pass


def is_shutting_down() -> bool:
    return _shutting_down


def register_shutdown_callback(fn: Callable[[], None]) -> None:
    """Register a callable to be invoked once set_shutting_down fires.

    Callbacks run in registration order on whatever thread first calls
    set_shutting_down. They should be small and non-blocking (typically
    flipping a module-level flag) — anything heavier should defer work
    off the signal-handling thread itself.

    If shutdown has already fired by the time this is called (late
    registration during a racing shutdown), the callback is invoked
    immediately so it cannot be lost.
    """
    global _shutdown_callbacks
    with _shutdown_lock:
        _shutdown_callbacks.append(fn)
        already_shutting = _shutting_down
    if already_shutting:
        try:
            fn()
        except Exception:  # pylint: disable=broad-except
            pass


def reset_shutting_down_for_tests() -> None:
    """Test helper — reset shutdown state to defaults."""
    global _shutting_down, _shutdown_callbacks
    with _shutdown_lock:
        _shutting_down = False
        _shutdown_callbacks = []

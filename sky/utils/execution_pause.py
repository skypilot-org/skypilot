"""Request-scoped opt-out for pausing execution (``ExecutionPausedError``).

``ExecutionPausedError`` releases the executor worker and re-queues the
*entire* request for a later re-run (see ``sky/server/requests/executor.py``).
That is only safe when the request body is idempotent and pause-transparent —
true for a bare ``sky.launch``, but not for a wrapper request that drives its
own state machine around ``execution.launch()`` and would find its own row
advanced past the state it expects on the re-run.

Such wrappers opt out via ``disallow_pause()`` around the inner call; every
site that raises ``ExecutionPausedError`` checks ``pause_allowed()`` first and
falls back to blocking in place — correct, at the cost of holding the worker
for the wait.

A module-level flag rather than a ContextVar: each executor worker process
runs a single request at a time, and a plain flag stays visible across any
threads the provisioner spawns, which a ContextVar would not.
"""

import contextlib
from typing import Iterator

_pause_disallowed: bool = False


@contextlib.contextmanager
def disallow_pause() -> Iterator[None]:
    """Mark execution in this block as unable to survive a pause re-queue."""
    global _pause_disallowed
    prev = _pause_disallowed
    _pause_disallowed = True
    try:
        yield
    finally:
        _pause_disallowed = prev


def pause_allowed() -> bool:
    """Whether raising ExecutionPausedError is safe for the current request."""
    return not _pause_disallowed

"""Resume conditions for paused request execution.

A request that pauses (``exceptions.ExecutionPausedError``) instead of holding
an executor worker may attach a ``ContinueCondition`` saying how to wait for
the resume signal. The scheduler calls ``wait()`` from the request's monitor
thread, so the worker is freed meanwhile. Subclass to own the polling/backoff/
fallback policy; instances are pickled onto the exception, so keep state
picklable and define subclasses in a module importable by the scheduler.

A ``wait()`` holds one OS thread for its whole duration, and parked requests
can outnumber executor workers by orders of magnitude (each is only waiting on
an external signal). An implementation may therefore also define
``wait_async()``, a coroutine the scheduler prefers and drives from a single
shared event loop, so any number of parked requests cost coroutines instead of
threads. Same signature and semantics as ``wait()``, with two differences:

* ``is_cancelled`` and ``update_status_msg`` are **async** callables
  (``await is_cancelled()``); the scheduler runs the blocking work behind
  them in a thread pool.
* The coroutine shares one event loop with every other parked request, so it
  must not block: sleep with ``asyncio.sleep`` and push any blocking probe
  (an HTTP poll, a DB read) through ``loop.run_in_executor``.

The contract stays duck-typed for cross-version tolerance: the scheduler uses
``wait_async`` only when the condition defines it as a coroutine function, and
falls back to running ``wait()`` in the monitor thread otherwise. The base
class deliberately does not define ``wait_async`` — inheriting a default here
would make every subclass look async-capable while hiding its overridden
``wait()`` policy.
"""
import time
from typing import Callable, Optional


class ContinueCondition:
    """A resumable wait attached to ``ExecutionPausedError``.

    Subclass and override ``wait()`` to customize how a paused request waits
    before being rescheduled; optionally also define ``wait_async()`` (see
    module docstring) so the wait does not hold an OS thread.
    """

    def wait(self,
             *,
             is_cancelled: Callable[[], bool],
             fallback_wait_seconds: float,
             update_status_msg: Optional[Callable[[str], None]] = None) -> bool:
        """Block until the paused request should resume.

        Returns True to reschedule, False to drop it (e.g. cancelled while
        paused, per ``is_cancelled``). ``fallback_wait_seconds`` is the default
        wait when there is no better signal.

        ``update_status_msg`` re-writes the parked request's status message
        (shown while the client waits) with a fresh reason. A wait that can
        last hours should call it whenever the reason changes, so the message
        does not go stale; the scheduler owns the formatting, so pass the bare
        reason. It is optional because the contract is duck-typed: the
        scheduler omits it for implementations whose ``wait()`` predates it.
        """
        del update_status_msg  # A fixed wait has no reason to report.
        # Default: one fixed wait, then reschedule unless cancelled.
        time.sleep(max(0, fallback_wait_seconds))
        return not is_cancelled()

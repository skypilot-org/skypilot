"""Resume conditions for paused request execution.

A request that pauses (``exceptions.ExecutionPausedError``) instead of holding
an executor worker may attach a ``ContinueCondition`` saying how to wait for
the resume signal. The scheduler calls ``wait()`` from the request's monitor
thread, so the worker is freed meanwhile. Subclass to own the polling/backoff/
fallback policy; instances are pickled onto the exception, so keep state
picklable and define subclasses in a module importable by the scheduler.
"""
import time
from typing import Callable


class ContinueCondition:
    """A resumable wait attached to ``ExecutionPausedError``.

    Subclass and override ``wait()`` to customize how a paused request waits
    before being rescheduled.
    """

    def wait(self, *, is_cancelled: Callable[[], bool],
             fallback_wait_seconds: float) -> bool:
        """Block until the paused request should resume.

        Returns True to reschedule, False to drop it (e.g. cancelled while
        paused, per ``is_cancelled``). ``fallback_wait_seconds`` is the default
        wait when there is no better signal.
        """
        # Default: one fixed wait, then reschedule unless cancelled.
        time.sleep(max(0, fallback_wait_seconds))
        return not is_cancelled()

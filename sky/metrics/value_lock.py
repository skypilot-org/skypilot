"""A lock for prometheus_client's multiprocess values that no thread sleeps on.

In multiprocess mode prometheus_client guards every value of a process with
one ``threading.Lock`` (``prometheus_client.values.MultiProcessValue``), on
the assumption that such a process runs no threads. An API server process
runs many: its request threads and its event loop all update metrics.

CPython hands a contended ``threading.Lock`` straight to a sleeping waiter,
which then has to get the GIL back before it can use the lock and release
it. Until it does, the lock is held by a thread that is not running, and
every other thread that updates a metric joins the queue behind it. While
another thread keeps the GIL busy (building a large response, a garbage
collection), each handover therefore costs one wait for the GIL, and a
request that updates a few dozen values can spend seconds in the queue.

``_SpinLock`` gives the same mutual exclusion without the sleep: a thread
that finds the lock held gives up the GIL and tries again. The lock is then
only ever taken by a running thread, which releases it microseconds later.

A leaf module, like ``sky.metrics.db``: the ``sky.metrics`` package imports
it first, so it imports nothing else from ``sky``.
"""
import logging
import threading
import time

from prometheus_client import values

logger = logging.getLogger(__name__)

# The closure variable of prometheus_client.values.MultiProcessValue that
# holds the lock shared by all of a process's values.
_LOCK_VARIABLE = 'lock'


class _SpinLock:
    """Mutual exclusion in which a waiter retries instead of sleeping."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self) -> None:
        while not self._lock.acquire(blocking=False):
            # Gives up the GIL, so the holder can run and release the lock.
            time.sleep(0)

    def __exit__(self, *exc_info) -> None:
        self._lock.release()


def install() -> None:
    """Makes this process's multiprocess values share a ``_SpinLock``.

    Does nothing outside multiprocess mode, where every value has a lock of
    its own. Call it before the process has threads that update metrics: a
    thread already waiting on the replaced lock would not exclude the
    threads that take the new one.
    """
    value_class = values.ValueClass
    if not value_class._multiprocess:  # pylint: disable=protected-access
        return
    inc = value_class.inc
    closure = inc.__closure__
    variables = inc.__code__.co_freevars
    if closure is None or _LOCK_VARIABLE not in variables:
        logger.warning(
            'prometheus_client.values.MultiProcessValue does not keep its '
            'lock in a closure variable named %r. Metric updates keep its '
            'own lock, on which threads can queue while the GIL is busy.',
            _LOCK_VARIABLE)
        return
    cell = closure[variables.index(_LOCK_VARIABLE)]
    if isinstance(cell.cell_contents, _SpinLock):
        return
    cell.cell_contents = _SpinLock()

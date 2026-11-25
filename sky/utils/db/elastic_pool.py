"""A connection pool that reuses connections and closes idle ones.

``QueuePool`` keeps ``pool_size`` connections and closes every overflow
connection as soon as it is returned, so a process that keeps bursting past
its pool size opens and closes a connection for most queries past it.

``ElasticQueuePool`` keeps every connection it opens, up to ``pool_size``, and
closes connections that sat idle for ``idle_timeout`` seconds until
``min_size`` remain. Connections are handed out LIFO, so the ones beyond
current demand stay at the bottom of the queue and are the ones a background
thread closes.
"""
import math
import threading
import time
from typing import Any, List, Optional
import weakref

from sqlalchemy import pool as sqlalchemy_pool

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# ConnectionPoolEntry.info key: time.monotonic() of the last check-in.
_IDLE_SINCE = 'sky_pool_idle_since'
_REAP_INTERVAL_MAX_SECONDS = 30.0


class ElasticQueuePool(sqlalchemy_pool.QueuePool):
    """QueuePool that keeps connections up to ``pool_size``, closing idle ones.

    ``max_overflow`` is always 0 and connections are reused LIFO. ``min_size``
    and ``idle_timeout`` are accepted by ``create_engine`` as plain keyword
    arguments.
    """

    def __init__(self,
                 creator: Any,
                 min_size: int = 0,
                 idle_timeout: float = (
                     constants.DEFAULT_DB_CONNECTION_POOL_IDLE_TIMEOUT_SECONDS),
                 **kw: Any) -> None:
        kw['max_overflow'] = 0
        kw['use_lifo'] = True
        super().__init__(creator, **kw)
        if self.size() < 1:
            raise ValueError(f'pool_size={self.size()} must be at least 1.')
        if not 0 <= min_size <= self.size():
            raise ValueError(f'min_size={min_size} must be between 0 and '
                             f'pool_size={self.size()}.')
        if not math.isfinite(idle_timeout) or idle_timeout <= 0:
            raise ValueError(f'idle_timeout={idle_timeout} must be a positive, '
                             'finite number of seconds.')
        self._min_size = min_size
        self._idle_timeout = idle_timeout
        # Serializes reaping with dispose(), which resets the open-connection
        # count that reaping decrements.
        self._reap_lock = threading.Lock()
        threading.Thread(target=_reap_forever,
                         args=(weakref.ref(self),),
                         name='db-pool-reaper',
                         daemon=True).start()

    def recreate(self) -> 'ElasticQueuePool':
        new_pool = super().recreate()
        assert isinstance(new_pool, ElasticQueuePool)
        new_pool._min_size = self._min_size  # pylint: disable=protected-access
        new_pool._idle_timeout = self._idle_timeout  # pylint: disable=protected-access
        return new_pool

    def dispose(self) -> None:
        with self._reap_lock:
            super().dispose()

    def _do_return_conn(self, record: Any) -> None:
        record.info[_IDLE_SINCE] = time.monotonic()
        super()._do_return_conn(record)

    def reap_idle(self, now: Optional[float] = None) -> int:
        """Close connections idle for ``idle_timeout``, keeping ``min_size``.

        Returns the number of connections closed.
        """
        if self._pool.maxsize + self._overflow <= self._min_size:
            return 0
        if now is None:
            now = time.monotonic()
        with self._reap_lock:
            idle: List[Any] = []
            queue = self._pool
            with queue.mutex:
                open_count = queue.maxsize + self._overflow
                # LIFO: index 0 is the connection returned longest ago.
                while (queue.queue and
                       open_count - len(idle) > self._min_size and
                       now - queue.queue[0].info.get(_IDLE_SINCE, now) >=
                       self._idle_timeout):
                    idle.append(queue.queue.popleft())
            for record in idle:
                try:
                    record.close()
                finally:
                    self._dec_overflow()
        return len(idle)


def _reap_forever(pool_ref: 'weakref.ref[ElasticQueuePool]') -> None:
    warned = False
    while True:
        pool = pool_ref()
        if pool is None:
            return
        try:
            pool.reap_idle()
        except Exception:  # pylint: disable=broad-except
            if not warned:
                logger.warning('Failed to close idle database connections.',
                               exc_info=True)
                warned = True
        interval = max(1.0,
                       min(_REAP_INTERVAL_MAX_SECONDS, pool._idle_timeout / 4))  # pylint: disable=protected-access
        del pool
        time.sleep(interval)

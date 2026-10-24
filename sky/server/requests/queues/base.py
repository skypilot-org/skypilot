"""Abstract interfaces for request queue backends."""

import abc
import multiprocessing
import time
from typing import Optional, Tuple

from sky import sky_logging
from sky.metrics import utils as metrics_utils
from sky.server.requests.queues import db_queue
from sky.utils.db import db_utils

logger = sky_logging.init_logger(__name__)


def _observe_queue_wait(schedule_type: str, enqueued_at: float) -> None:
    """Record time the just-dequeued entry spent waiting in the queue.

    Metric emission must never disrupt the dequeue path, so any failure
    (label mismatch, client error) is logged and swallowed.
    """
    if not metrics_utils.METRICS_ENABLED:
        return
    try:
        metrics_utils.SKY_APISERVER_QUEUE_WAIT_SECONDS.labels(
            schedule_type=schedule_type).observe(
                max(0,
                    time.time() - enqueued_at))
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Failed to observe queue wait metric: {e}')


class QueueBackend(abc.ABC):
    """Abstract queue backend.

    Each backend is responsible for emitting
    ``SKY_APISERVER_QUEUE_WAIT_SECONDS`` from ``get()`` (measuring the time
    between enqueue and dequeue of the returned entry), so the metric reflects
    true queue residency regardless of the backend's storage.
    """

    @abc.abstractmethod
    def put(self, item: Tuple[str, bool, bool]) -> None:
        """Put a (request_id, ignore_return_value, retryable) tuple."""
        raise NotImplementedError

    async def put_async(self, item: Tuple[str, bool, bool]) -> None:
        """Async version of put."""
        # By default we assume put is not blocking and can be
        # called directly in event loop
        self.put(item)

    @abc.abstractmethod
    def get(self) -> Optional[Tuple[str, bool, bool]]:
        """Non-blocking get. Returns None if queue is empty.

        Implementations must emit ``SKY_APISERVER_QUEUE_WAIT_SECONDS`` for the
        dequeued entry (see ``_observe_queue_wait``).
        """
        raise NotImplementedError

    @abc.abstractmethod
    def qsize(self) -> int:
        """Return approximate queue size."""
        raise NotImplementedError


class QueueBackendFactory(abc.ABC):
    """Creates queue instances and manages queue infrastructure."""

    @abc.abstractmethod
    def create_queue(self, schedule_type: str) -> QueueBackend:
        """Create a queue for the given schedule type.

        Args:
            schedule_type: The schedule type string (e.g., 'long', 'short').
        """
        raise NotImplementedError

    def start(self) -> Optional[multiprocessing.Process]:
        """Start any required background infrastructure.

        Returns:
            A process to join on shutdown, or None if no background process
            is needed.
        """
        return None

    def stop(self, process: Optional[multiprocessing.Process]) -> None:
        """Cleanup infrastructure."""
        if process is not None:
            process.kill()


class DbQueueBackend(QueueBackend):
    """Queue stored in the request database (see ``db_queue``)."""

    def __init__(self, schedule_type: str):
        super().__init__()
        # Also the metric label when reporting queue wait.
        self._schedule_type = schedule_type
        # SQLiteConn keeps one connection per thread.
        self._db = db_utils.SQLiteConn(db_queue.get_db_path(),
                                       db_queue.create_table)

    def put(self, item: Tuple[str, bool, bool]) -> None:
        db_queue.put(self._db, self._schedule_type, item)

    async def put_async(self, item: Tuple[str, bool, bool]) -> None:
        await db_queue.put_async(self._db, self._schedule_type, item)

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        claimed = db_queue.claim(self._db, self._schedule_type)
        if claimed is None:
            return None
        item, enqueued_at = claimed
        _observe_queue_wait(self._schedule_type, enqueued_at)
        return item

    def qsize(self) -> int:
        return db_queue.size(self._db, self._schedule_type)


class DbQueueFactory(QueueBackendFactory):
    """Factory for queues stored in the request database.

    The database is the shared state, so there is no background process.
    """

    def create_queue(self, schedule_type: str) -> QueueBackend:
        return DbQueueBackend(schedule_type)


_queue_backend_factory: Optional[QueueBackendFactory] = None


def get_queue_backend_factory() -> QueueBackendFactory:
    """Get the registered queue backend factory."""
    if _queue_backend_factory is not None:
        return _queue_backend_factory
    return DbQueueFactory()


def set_queue_backend_factory(factory: QueueBackendFactory) -> None:
    """Set the queue backend factory."""
    global _queue_backend_factory
    _queue_backend_factory = factory

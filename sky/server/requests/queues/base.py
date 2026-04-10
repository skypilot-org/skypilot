"""Abstract interfaces for request queue backends.

The default implementations wrap the existing local_queue and mp_queue
modules. Plugins may provide alternative implementations (e.g., PostgreSQL
SKIP LOCKED queues) via ExtensionContext.register_queue_backend_factory().
"""
import abc
import multiprocessing
import queue as queue_lib
from typing import List, Optional, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class QueueBackend(abc.ABC):
    """Abstract queue that the RequestWorker polls.

    Queue items are tuples of (request_id, ignore_return_value, retryable).
    """

    @abc.abstractmethod
    def put(self, item: Tuple[str, bool, bool]) -> None:
        """Put a (request_id, ignore_return_value, retryable) tuple."""
        raise NotImplementedError

    @abc.abstractmethod
    def get(self) -> Optional[Tuple[str, bool, bool]]:
        """Non-blocking get. Returns None if queue is empty."""
        raise NotImplementedError

    @abc.abstractmethod
    def qsize(self) -> int:
        """Return approximate queue size."""
        raise NotImplementedError


class QueueBackendFactory(abc.ABC):
    """Creates queue instances and manages queue infrastructure.

    The factory is responsible for any required infrastructure (e.g., starting
    a queue manager process for multiprocessing queues).
    """

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


# ---------------------------------------------------------------------------
# Default implementations wrapping existing local_queue / mp_queue
# ---------------------------------------------------------------------------


class LocalQueueBackend(QueueBackend):
    """Process-local queue (thread-safe, no IPC)."""

    def __init__(self, queue_name: str):
        from sky.server.requests.queues import local_queue
        self._queue = local_queue.get_queue(queue_name)

    def put(self, item: Tuple[str, bool, bool]) -> None:
        self._queue.put(item)

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        try:
            return self._queue.get(block=False)
        except queue_lib.Empty:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()


class MultiprocessingQueueBackend(QueueBackend):
    """Queue backed by a multiprocessing.Queue via a manager."""

    def __init__(self, queue_name: str, port: int):
        from sky.server.requests.queues import mp_queue
        self._queue = mp_queue.get_queue(queue_name, port)

    def put(self, item: Tuple[str, bool, bool]) -> None:
        self._queue.put(item)

    def get(self) -> Optional[Tuple[str, bool, bool]]:
        try:
            return self._queue.get(block=False)
        except queue_lib.Empty:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()


class LocalQueueFactory(QueueBackendFactory):
    """Factory for process-local queues."""

    def create_queue(self, schedule_type: str) -> QueueBackend:
        return LocalQueueBackend(schedule_type)


class MultiprocessingQueueFactory(QueueBackendFactory):
    """Factory for multiprocessing queues with a shared manager."""

    def __init__(self, port: Optional[int] = None):
        from sky.server.requests.queues import mp_queue
        self._port = (port if port is not None else
                      mp_queue.DEFAULT_QUEUE_MANAGER_PORT)

    def create_queue(self, schedule_type: str) -> QueueBackend:
        return MultiprocessingQueueBackend(schedule_type, self._port)

    def start(self) -> Optional[multiprocessing.Process]:
        from sky.server.requests.queues import mp_queue
        from sky.utils import common_utils

        if not common_utils.is_port_available(self._port):
            raise RuntimeError(
                f'SkyPilot API server fails to start as port {self._port!r} '
                'is already in use by another process.')

        queue_names = self._get_queue_names()
        process = multiprocessing.Process(target=mp_queue.start_queue_manager,
                                          args=(queue_names, self._port))
        process.start()
        mp_queue.wait_for_queues_to_be_ready(queue_names,
                                             process,
                                             port=self._port)
        return process

    @staticmethod
    def _get_queue_names() -> List[str]:
        from sky.server.requests import requests as api_requests
        return [st.value for st in api_requests.ScheduleType]


_queue_backend_factory: Optional[QueueBackendFactory] = None


def get_queue_backend_factory() -> Optional[QueueBackendFactory]:
    """Get the registered queue backend factory.

    Returns the plugin-provided factory if one has been registered,
    otherwise returns None (caller should create based on server config).
    """
    return _queue_backend_factory


def set_queue_backend_factory(factory: QueueBackendFactory) -> None:
    """Set the queue backend factory.

    Called by plugins via ExtensionContext.register_queue_backend_factory().
    """
    global _queue_backend_factory
    _queue_backend_factory = factory

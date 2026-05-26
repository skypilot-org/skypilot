"""Abstract interfaces for request queue backends."""

import abc
import multiprocessing
import queue as queue_lib
import time
from typing import List, Optional, Tuple

import psutil

from sky import sky_logging
from sky.server.requests import requests as api_requests
from sky.server.requests.queues import local_queue
from sky.server.requests.queues import mp_queue
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)


def _reap_orphan_queue_manager(port: int) -> bool:
    """If port is held by an orphaned queue manager subprocess, kill it.

    The SkyPilot API server starts its request queue manager as a
    multiprocessing subprocess on a fixed port. If the API server is killed
    abruptly (SIGKILL, OOM, hard reboot) the subprocess is reparented to
    PID 1 and keeps running, blocking later attempts to start the API
    server with "port already in use". This is the cause of repeated
    SkyServe AWS replica FAILED_PROVISION on long-lived controller VMs
    where the API server has been killed at least once.

    Newer queue managers self-terminate via PR_SET_PDEATHSIG so this only
    matters for cleaning up orphans created by older versions, but the
    safeguard is cheap and not Linux-specific.

    Returns:
        True if an orphan was found and killed, False otherwise.
    """
    # Find the process that owns the port. psutil.net_connections() requires
    # privileges on macOS but works as the same user on Linux for sockets
    # owned by that user, which is the relevant case here.
    holder_pid: Optional[int] = None
    try:
        for conn in psutil.net_connections(kind='tcp'):
            if (conn.status == psutil.CONN_LISTEN and conn.laddr and
                    conn.laddr.port == port and conn.pid is not None):
                holder_pid = conn.pid
                break
    except (psutil.AccessDenied, PermissionError) as e:
        logger.debug(f'Cannot enumerate net_connections to find port {port} '
                     f'holder: {e}')
        return False
    if holder_pid is None:
        return False

    try:
        holder = psutil.Process(holder_pid)
        cmdline = holder.cmdline()
        parent_pid = holder.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        logger.debug(f'Could not inspect pid {holder_pid} holding port '
                     f'{port}: {e}')
        return False

    # Heuristic: the orphan is a python child started via multiprocessing.
    # Its cmdline contains "multiprocessing.spawn" (CPython convention) and
    # its parent is PID 1 (init / reaped). We refuse to kill anything that
    # doesn't match both signals so we never harm an unrelated process that
    # happens to be on this port.
    cmdline_str = ' '.join(cmdline)
    if 'multiprocessing' not in cmdline_str:
        logger.warning(f'Port {port} is held by pid {holder_pid} '
                       f'({cmdline_str!r}); not a SkyPilot queue manager, '
                       f'leaving it alone.')
        return False
    if parent_pid != 1:
        logger.warning(f'Port {port} is held by pid {holder_pid} '
                       f'(parent={parent_pid}); not an orphan, leaving it '
                       f'alone.')
        return False

    logger.warning(
        f'Port {port} is held by an orphaned multiprocessing subprocess '
        f'(pid {holder_pid}, parent died and reaped to init). This is '
        f'almost certainly a leaked queue manager from a previous '
        f'SkyPilot API server that was killed abruptly. Reaping it now so '
        f'the new API server can start.')
    try:
        holder.terminate()
        try:
            holder.wait(timeout=3)
        except psutil.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=2)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        logger.warning(f'Failed to reap orphan pid {holder_pid}: {e}')
        return False
    return True


class QueueBackend(abc.ABC):
    """Abstract queue backend."""

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
        """Non-blocking get. Returns None if queue is empty."""
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


class LocalQueueBackend(QueueBackend):
    """Process-local queue (thread-safe, no IPC)."""

    def __init__(self, queue_name: str):
        super().__init__()
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

    def __init__(self,
                 queue_name: str,
                 port: int = mp_queue.DEFAULT_QUEUE_MANAGER_PORT):
        super().__init__()
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
        super().__init__()
        self._port = (port if port is not None else
                      mp_queue.DEFAULT_QUEUE_MANAGER_PORT)

    def create_queue(self, schedule_type: str) -> QueueBackend:
        return MultiprocessingQueueBackend(schedule_type, self._port)

    def start(self) -> Optional[multiprocessing.Process]:

        if not common_utils.is_port_available(self._port):
            # The most common reason this happens in practice is a leaked
            # queue manager from a previously-killed API server (the
            # subprocess gets reparented to init and survives). Try to
            # reap such an orphan and then re-check; if the port is held
            # by something else we still error out so we don't mask a
            # genuine conflict.
            if _reap_orphan_queue_manager(self._port):
                # Give the kernel a moment to release the bind.
                for _ in range(20):
                    if common_utils.is_port_available(self._port):
                        break
                    time.sleep(0.1)
            if not common_utils.is_port_available(self._port):
                raise RuntimeError(
                    f'SkyPilot API server fails to start as port '
                    f'{self._port!r} is already in use by another process.')

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
        return [st.value for st in api_requests.ScheduleType]


_queue_backend_factory: Optional[QueueBackendFactory] = None


def get_queue_backend_factory() -> QueueBackendFactory:
    """Get the registered queue backend factory."""
    if _queue_backend_factory is not None:
        return _queue_backend_factory
    return MultiprocessingQueueFactory()


def set_queue_backend_factory(factory: QueueBackendFactory) -> None:
    """Set the queue backend factory."""
    global _queue_backend_factory
    _queue_backend_factory = factory

"""Abstract interface for request persistence."""

from __future__ import annotations

import abc
import contextlib
from typing import (AsyncGenerator, Generator, List, Optional, Set, Tuple,
                    TYPE_CHECKING)

if TYPE_CHECKING:
    from sky.server import daemons as daemons_lib
    from sky.server.requests.requests import Request
    from sky.server.requests.requests import RequestStatus
    from sky.server.requests.requests import RequestTaskFilter
    from sky.server.requests.requests import StatusWithMsg


class RequestBackend(abc.ABC):
    """Abstract interface for request persistence and lifecycle."""

    @abc.abstractmethod
    def get_request(self,
                    request_id: str,
                    fields: Optional[List[str]] = None) -> Optional[Request]:
        """Get a request by ID with appropriate locking."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_request_async(
            self,
            request_id: str,
            fields: Optional[List[str]] = None) -> Optional[Request]:
        """Async version of get_request."""
        raise NotImplementedError

    @abc.abstractmethod
    @contextlib.contextmanager
    def update_request(
            self, request_id: str) -> Generator[Optional[Request], None, None]:
        """Atomic read-modify-write with appropriate locking.

        Yields the request object. Caller modifies it in-place. On context
        exit, the modified request is persisted. If the request doesn't exist,
        yields None.
        """
        raise NotImplementedError

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def update_request_async(
            self, request_id: str) -> AsyncGenerator[Optional[Request], None]:
        """Async version of update_request."""
        del request_id
        yield None

    @abc.abstractmethod
    async def create_if_not_exists_async(self, request: Request) -> bool:
        """Create a request if it does not exist.

        Returns:
            True if a new request was created, False if it already exists.
        """
        raise NotImplementedError

    async def reconcile_internal_daemons_async(
        self,
        internal_daemons: List['daemons_lib.InternalRequestDaemon'],
    ) -> List['Request']:
        """Reconcile persisted daemon rows with `internal_daemons`.

        The default behaviour (used by the OSS SQLite backend, whose DB
        is wiped via ``reset_db_and_logs`` ->
        ``clear_local_api_server_database`` on every ``sky api start``)
        is to insert a fresh PENDING row
        for each non-skip daemon and return the list of `Request`
        objects for the caller to enqueue onto an in-memory task queue.
        There is no stale row state to reconcile because the DB is
        freshly empty.

        Backends with a persistent task_queue (PG) override this method
        to refresh env_vars on existing rows (so they reflect the
        current process's `os.environ` rather than whatever the row's
        original creator captured), delete daemon-shaped rows whose ids
        are no longer in `internal_daemons`, preserve rows currently
        RUNNING on a live executor (refresh env_vars only), and
        maintain a single `task_queue` entry per daemon inside the same
        transaction. The PG override returns `[]` because it writes the
        queue inline; the SQLite default returns the full set of
        non-skip daemons for the caller to enqueue.
        """
        # pylint: disable=import-outside-toplevel
        from sky.server.requests import requests as requests_lib
        to_enqueue: List['Request'] = []
        for d in internal_daemons:
            if d.should_skip():
                continue
            req = requests_lib.build_internal_daemon_request(d)
            await self.create_if_not_exists_async(req)
            to_enqueue.append(req)
        return to_enqueue

    @abc.abstractmethod
    def query_requests(self, req_filter: RequestTaskFilter) -> List[Request]:
        """Query requests matching the filter."""
        raise NotImplementedError

    @abc.abstractmethod
    async def query_requests_async(
            self, req_filter: RequestTaskFilter) -> List[Request]:
        """Async version of query_requests."""
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_requests(self, request_ids: List[str]) -> None:
        """Delete requests by their IDs."""
        raise NotImplementedError

    @abc.abstractmethod
    async def update_status_async(self, request_id: str,
                                  status: RequestStatus) -> None:
        """Update the status of a request."""
        raise NotImplementedError

    @abc.abstractmethod
    async def update_status_msg_async(self, request_id: str,
                                      status_msg: str) -> None:
        """Update the status message of a request."""
        raise NotImplementedError

    @abc.abstractmethod
    def kill_requests(self,
                      request_ids: Optional[List[str]] = None,
                      user_id: Optional[str] = None) -> List[str]:
        """Kill requests and set their status to CANCELLED.

        Returns:
            A list of request IDs that were cancelled.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def kill_request_async(self, request_id: str) -> bool:
        """Kill a single request and set its status to cancelled.

        Returns:
            True if the request was killed, False otherwise.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_latest_request_id_async(self) -> Optional[str]:
        """Get the most recent request ID."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_requests_with_prefix(
            self,
            request_id_prefix: str,
            fields: Optional[List[str]] = None) -> Optional[List[Request]]:
        """Get all requests matching an ID prefix."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_requests_async_with_prefix(
            self,
            request_id_prefix: str,
            fields: Optional[List[str]] = None) -> Optional[List[Request]]:
        """Async version of get_requests_with_prefix."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_request_status_async(
            self,
            request_id: str,
            include_msg: bool = False) -> Optional[StatusWithMsg]:
        """Get the status (and optionally status_msg) of a request."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_api_request_ids_start_with(self,
                                             incomplete: str) -> List[str]:
        """Get request IDs for shell completion."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_active_file_mounts_blob_ids(self) -> Set[str]:
        """Get blob IDs referenced by active (PENDING/RUNNING) requests."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_shutdown_active_requests(self) -> List[Tuple[str, str]]:
        """Get (request_id, name) pairs to wait for during graceful shutdown."""
        raise NotImplementedError

    def reset_on_startup(self) -> None:
        """Called on server startup for backend-specific initialization."""


_storage_backend: Optional[RequestBackend] = None


def get_request_backend() -> RequestBackend:
    """Get the registered request backend."""
    global _storage_backend
    if _storage_backend is None:
        # pylint: disable=import-outside-toplevel
        from sky.server.requests.requests import SqliteRequestBackend

        _storage_backend = SqliteRequestBackend()
    return _storage_backend


def set_request_backend(backend: RequestBackend) -> None:
    """Set the request backend."""
    global _storage_backend
    _storage_backend = backend

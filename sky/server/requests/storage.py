"""Abstract interface for request persistence.

The default implementation uses SQLite with file-based locking.
Plugins may provide alternative implementations (e.g., PostgreSQL)
via ExtensionContext.register_request_storage().
"""
from __future__ import annotations

import abc
import contextlib
from typing import AsyncGenerator, Generator, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from sky.server.requests.requests import Request
    from sky.server.requests.requests import RequestStatus
    from sky.server.requests.requests import RequestTaskFilter
    from sky.server.requests.requests import StatusWithMsg


class RequestBackend(abc.ABC):
    """Abstract interface for request persistence and lifecycle."""

    # --- Core CRUD ---

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
        raise NotImplementedError
        yield  # pylint: disable=unreachable

    @abc.abstractmethod
    async def create_if_not_exists_async(self, request: Request) -> bool:
        """Create a request if it does not exist.

        Returns:
            True if a new request was created, False if it already exists.
        """
        raise NotImplementedError

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

    # --- Status updates ---

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

        If request_ids is None, kills all PENDING/RUNNING requests for
        the given user_id (or all users if user_id is also None).

        For the default (SQLite) implementation, this sends SIGTERM to
        local request processes. Alternative implementations may handle
        cross-replica cancellation (e.g., setting status in DB for the
        owning replica's heartbeat to detect and kill).

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

    # --- Specialized queries ---

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

    # --- Lifecycle ---

    def reset_on_startup(self) -> None:
        """Called on server startup for backend-specific initialization.

        The default SQLite implementation re-creates the database and checks
        the SQLite version. Alternative backends may run migrations, mark
        stale requests as failed, etc.
        """


# Backward-compat alias
RequestStorageBackend = RequestBackend

_storage_backend: Optional[RequestBackend] = None


def get_request_backend() -> RequestBackend:
    """Get the registered request backend.

    Returns the plugin-provided backend if one has been registered,
    otherwise lazily creates and returns the default SqliteRequestBackend.
    """
    global _storage_backend
    if _storage_backend is None:
        # Lazy import to avoid circular dependency: storage.py is imported
        # by requests.py, and SqliteRequestBackend is defined in requests.py.
        # pylint: disable-next=import-outside-toplevel
        from sky.server.requests.requests import SqliteRequestBackend
        _storage_backend = SqliteRequestBackend()
    return _storage_backend


# Backward-compat alias
get_request_storage = get_request_backend


def set_request_backend(backend: RequestBackend) -> None:
    """Set the request backend.

    Called by plugins via ExtensionContext.register_request_storage().
    """
    global _storage_backend
    _storage_backend = backend


# Backward-compat alias
set_request_storage = set_request_backend

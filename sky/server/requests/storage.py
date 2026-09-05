"""Abstract interface for request persistence."""

from __future__ import annotations

import abc
import contextlib
from typing import (AsyncGenerator, Generator, List, Optional, Set, Tuple,
                    TYPE_CHECKING)

if TYPE_CHECKING:
    from sky.server import daemons as daemons_lib
    from sky.server.requests import payloads as payloads_lib
    from sky.server.requests.payloads import RequestPayload
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

    @abc.abstractmethod
    async def create_or_refresh_internal_daemon_async(
            self, request: 'Request') -> bool:
        """For an internal daemon request: insert a fresh PENDING row or
        refresh env-bearing columns on an existing row.

        Returns True if a new row was inserted (caller should enqueue
        the request onto the task queue), False if an existing row was
        refreshed in-place (the task_queue entry from the original
        creator stays in place; do NOT enqueue again).

        Atomic + idempotent under concurrent callers. Replaces
        `create_if_not_exists_async` on the daemon submission path:
        the dedup contract is identical (exactly one concurrent caller
        gets True), but losing callers also UPDATE `request_body`,
        `name`, and `schedule_type` on the existing row so the
        persisted `env_vars` reflect the current process's
        `os.environ` rather than whatever the original creator
        captured (which may be from a previous deployment generation
        in HA setups).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_orphan_internal_daemons_async(
        self,
        internal_daemons: List['daemons_lib.InternalRequestDaemon'],
    ) -> None:
        """Delete daemon-shaped rows whose `request_id` is not in
        `internal_daemons` (daemon was renamed / removed in code),
        along with any task_queue entries (for backends with a
        persistent queue).

        Idempotent under concurrent callers.
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

    async def query_request_payloads_async(
            self,
            req_filter: 'RequestTaskFilter',
            caller_user_id: Optional[str] = None,
            omit_unrequested: bool = False,  # pylint: disable=unused-argument
    ) -> List['RequestPayload']:
        """Fields-aware fast path for ``/api/status`` display listings.

        Backends that want the fast path -- building display
        ``RequestPayload``\\ s straight from projected rows, skipping the
        per-row ``Request.from_row`` decode + ``encode_requests``
        re-validation -- override this, honoring ``omit_unrequested`` (trim
        the wire for new clients vs the full legacy wire for older ones).
        The default ignores ``omit_unrequested`` and falls back to the legacy
        decode path (``query_requests_async`` + ``encode_requests``), which
        always emits the full wire, so a backend that does not override it
        (e.g. an unshipped HA Postgres backend) keeps the current behavior
        with no regression. This is a concrete, non-abstract method precisely
        so a backend is not forced to implement it in lockstep with the OSS
        change.
        """
        # Lazy import: sky.server.requests.requests imports this module for the
        # RequestBackend ABC, so importing it at module top would cycle.
        # pylint: disable=import-outside-toplevel
        from sky.server.requests import requests as api_requests
        decoded = await self.query_requests_async(req_filter)
        return api_requests.encode_requests(decoded,
                                            caller_user_id=caller_user_id)

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

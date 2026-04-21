"""External managed job runner interface for plugins.

This module provides an extension point that lets plugins supply an
alternative implementation for the three managed-job operations that
normally round-trip to the controller via codegen + subprocess:

- fetch_managed_job_table (queue)
- cancel_managed_jobs
- tail_managed_job_logs

When a plugin registers an implementation, the corresponding helpers in
``sky.jobs.server.core`` delegate to the registered functions instead of
the default subprocess path. Plugins are expected to register only when
they know every call will be handled correctly (for example, when
consolidation mode is active and the controller runs in-process).

Example usage in a plugin::

    from sky.utils.plugin_extensions import ExternalManagedJobRunner

    ExternalManagedJobRunner.register(
        fetch=my_fetch_fn,
        cancel=my_cancel_fn,
        tail_logs=my_tail_logs_fn,
    )

Example usage in core SkyPilot::

    from sky.utils.plugin_extensions import ExternalManagedJobRunner

    if ExternalManagedJobRunner.is_registered():
        return ExternalManagedJobRunner.fetch_managed_job_table(**kwargs)
    # else fall back to the default subprocess path
"""
from typing import Any, Dict, List, Optional, Protocol, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


# Protocol definitions for the registered functions.
class FetchManagedJobTableFunc(Protocol):
    """Protocol for the fetch function.

    Must return ``(jobs, total, result_type, total_no_filter, status_counts)``
    matching ``_fetch_managed_job_table_via_controller``'s return type.
    """

    def __call__(
        self,
        *,
        handle: Any,
        backend: Any,
        skip_finished: bool,
        accessible_workspaces: List[str],
        job_ids: Optional[List[int]],
        workspace_match: Optional[str],
        name_match: Optional[str],
        pool_match: Optional[str],
        page: Optional[int],
        limit: Optional[int],
        user_hashes: Optional[List[Optional[str]]],
        statuses: Optional[List[str]],
        fields: Optional[List[str]],
        sort_by: Optional[str],
        sort_order: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], int, Any, int, Dict[str, int]]:
        ...


class CancelManagedJobsFunc(Protocol):
    """Protocol for the cancel function. Returns stdout string."""

    def __call__(
        self,
        *,
        handle: Any,
        backend: Any,
        all_users: bool,
        all: bool,  # pylint: disable=redefined-builtin
        job_ids: Optional[List[int]],
        name: Optional[str],
        pool: Optional[str],
        graceful: bool,
        graceful_timeout: Optional[int],
    ) -> str:
        ...


class TailManagedJobLogsFunc(Protocol):
    """Protocol for the tail_logs function. Returns exit code."""

    def __call__(
        self,
        *,
        handle: Any,
        backend: Any,
        job_id: Optional[int],
        job_name: Optional[str],
        follow: bool,
        controller: bool,
        tail: Optional[int],
        task: Any,
    ) -> int:
        ...


class ExternalManagedJobRunner:
    """Singleton registry for an alternative managed-job runner.

    Plugins register an implementation during their ``install()`` phase.
    Core SkyPilot checks ``is_registered()`` and calls the corresponding
    dispatch method when a registration exists. At most one implementation
    may be registered at a time.
    """

    _fetch_func: Optional[FetchManagedJobTableFunc] = None
    _cancel_func: Optional[CancelManagedJobsFunc] = None
    _tail_logs_func: Optional[TailManagedJobLogsFunc] = None

    @classmethod
    def register(
        cls,
        *,
        fetch: FetchManagedJobTableFunc,
        cancel: CancelManagedJobsFunc,
        tail_logs: TailManagedJobLogsFunc,
    ) -> None:
        """Register an external managed job runner.

        All three functions must be provided together. A subsequent
        ``register`` call replaces the previous registration.
        """
        cls._fetch_func = fetch
        cls._cancel_func = cancel
        cls._tail_logs_func = tail_logs
        logger.debug('Registered external managed job runner')

    @classmethod
    def is_registered(cls) -> bool:
        """Return True when all three functions have been registered."""
        return (cls._fetch_func is not None and cls._cancel_func is not None and
                cls._tail_logs_func is not None)

    @classmethod
    def fetch_managed_job_table(
        cls, **kwargs: Any
    ) -> Tuple[List[Dict[str, Any]], int, Any, int, Dict[str, int]]:
        """Dispatch to the registered fetch function."""
        assert cls._fetch_func is not None, (
            'ExternalManagedJobRunner.fetch_managed_job_table called with no '
            'fetch function registered')
        return cls._fetch_func(**kwargs)  # pylint: disable=not-callable

    @classmethod
    def cancel_managed_jobs(cls, **kwargs: Any) -> str:
        """Dispatch to the registered cancel function."""
        assert cls._cancel_func is not None, (
            'ExternalManagedJobRunner.cancel_managed_jobs called with no '
            'cancel function registered')
        return cls._cancel_func(**kwargs)  # pylint: disable=not-callable

    @classmethod
    def tail_managed_job_logs(cls, **kwargs: Any) -> int:
        """Dispatch to the registered tail_logs function."""
        assert cls._tail_logs_func is not None, (
            'ExternalManagedJobRunner.tail_managed_job_logs called with no '
            'tail_logs function registered')
        return cls._tail_logs_func(**kwargs)  # pylint: disable=not-callable

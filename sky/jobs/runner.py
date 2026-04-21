"""Managed job runner: abstraction + registry.

A ``ManagedJobRunner`` is the strategy object that the server's managed-job
entry points (queue, cancel, tail_logs) delegate to. The registered runner
decides *how* the operation executes — the default runner generates Python
code and runs it on the controller via subprocess, while a plugin-provided
runner might call the managed jobs DB directly when the controller is
in-process.

At most one runner is registered at a time. The default is installed by
``sky.jobs.server.core`` at module import. Plugins override the default
by calling ``register(MyRunner())`` in their ``install()`` phase.
"""
from typing import Any, Dict, List, Optional, Protocol, Tuple

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class ManagedJobRunner(Protocol):
    """Strategy interface for managed job controller operations."""

    def fetch_managed_job_table(
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

    def cancel_managed_jobs(
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

    def tail_managed_job_logs(
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


_current: Optional[ManagedJobRunner] = None


def register(runner: ManagedJobRunner) -> None:
    """Install ``runner`` as the currently-active managed job runner.

    Last registration wins. The default runner is registered on import
    of ``sky.jobs.server.core``; plugins override in ``install()``.
    """
    # pylint: disable=global-statement
    global _current
    _current = runner
    logger.debug('Registered ManagedJobRunner: %s', type(runner).__name__)


def current() -> ManagedJobRunner:
    """Return the currently-registered runner.

    Raises ``AssertionError`` if no runner has been registered. That
    should not happen in practice: importing ``sky.jobs.server.core``
    registers the default runner as a side effect.
    """
    assert _current is not None, (
        'No ManagedJobRunner has been registered. Importing '
        'sky.jobs.server.core should register the default runner.')
    return _current

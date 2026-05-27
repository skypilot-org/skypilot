"""Run the managed-job-status-refresh loop as a thread in the API server."""
import os
import pathlib
import signal
import threading
import time
import typing
from typing import Optional

from sky import sky_logging
from sky.skylet import constants
from sky.utils import locks
from sky.jobs import utils as managed_job_utils
from sky.skylet import events

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

_LOCK_PROBE_INTERVAL_SECONDS = 5
_ACQUIRE_RETRY_INTERVAL_SECONDS = 5


class ManagedJobRefreshDaemonThread(threading.Thread):
    """Leader-elected thread that runs ha_recovery + ManagedJobEvent.

    See module docstring for motivation and invariants.
    """

    def __init__(self) -> None:
        # daemon=True: when the main interpreter exits we want this thread
        # to go with it; the leader role is meant to track main's lifecycle.
        super().__init__(name='managed-job-refresh', daemon=True)
        self._lock: Optional[locks.DistributedLock] = None

    def run(self) -> None:
        from sky.jobs import constants as managed_job_constants

        self._lock = locks.get_lock(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)

        while True:
            try:
                self._become_leader_and_run()
            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception as e:  # pylint: disable=broad-except
                logger.exception(
                    f'managed-job refresh error: {e}, '
                    f'retrying in {_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)


    def _become_leader_and_run(self) -> None:
        # pylint: disable=import-outside-toplevel

        assert self._lock is not None

        # Touch the rolling-restart signal file before acquiring the
        # lock.  Same semantics as the historical event_fn: any peer
        # checking this file sees "leader handover in progress" until
        # we've finished recovery.
        signal_file = pathlib.Path(
            constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
        signal_file.touch()
        try:
            if not self._lock.is_locked():
                logger.info(
                    f'Acquiring the consolidation mode lock: {self._lock}')
                self._lock.acquire()
                logger.info('Consolidation mode lock acquired')
            # Recovery before any event-loop work: same ordering as the
            # original ``managed_job_status_refresh_event``.
            managed_job_utils.ha_recovery_for_consolidation_mode()
        finally:
            try:
                signal_file.unlink()
            except FileNotFoundError:
                pass

        # Steady-state event loop with periodic lock-liveness probe.
        refresh_event = events.ManagedJobEvent()
        last_probe = time.monotonic()
        while True:
            now = time.monotonic()
            if now - last_probe >= _LOCK_PROBE_INTERVAL_SECONDS:
                if not self._lock_still_held():
                    self._suicide_on_lock_loss()
                    return
                last_probe = now
            try:
                logger.debug('=== Running managed job event ===')
                refresh_event.run()
            except Exception:  # pylint: disable=broad-except
                # Tick failures are non-fatal — mirror the old
                # InternalRequestDaemon.run_event catch-all.  Lock loss
                # is the only thing that should take the pod down.
                logger.exception('ManagedJobEvent tick failed; will retry')
            time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)

    def _lock_still_held(self) -> bool:
        """True iff we are confident this replica still owns the lock."""
        assert self._lock is not None
        if isinstance(self._lock, locks.PostgresLock):
            # Check is only relevant for PG lock
            return self._lock.is_session_alive()
        return True

    def _suicide_on_lock_loss(self) -> None:
        """SIGTERM the API server process so the pod can restart cleanly."""
        logger.error(
            f'Lost consolidation mode lock {self._lock}; sending SIGTERM '
            'to the API server to step down')
        # SIGTERM to trigger graceful shutdown
        os.kill(os.getpid(), signal.SIGTERM)


def start_managed_job_refresh_daemon() -> None:
    """Start the refresh thread for this API server process, if needed.

    No-op when consolidation mode is off — mirrors the gating that the
    historical ``should_skip_managed_job_status_refresh`` provided.
    """
    if not managed_job_utils.is_consolidation_mode():
        logger.debug('Consolidation mode is off; not starting the managed-job '
                     'refresh thread.')
        return
    logger.info('Starting the managed-job refresh thread')
    ManagedJobRefreshDaemonThread().start()

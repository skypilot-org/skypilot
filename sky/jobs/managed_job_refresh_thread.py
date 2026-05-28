"""Run the managed-job-status-refresh loop as a thread in the API server."""
import os
import pathlib
import signal
import threading
import time
import typing
from typing import Optional

from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler as managed_job_scheduler
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants
from sky.skylet import events
from sky.utils import locks

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
        self._lock = locks.get_lock(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)

        while True:
            try:
                self._become_leader_and_run()
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    'managed-job refresh error; '
                    f'retrying in {_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                # If we previously held the lock and lost the session
                # mid-recovery, retrying would run as a stale leader
                # (local `_acquired` flag still True, server-side lock
                # released, another replica can grab it).  Hand off via
                # SIGTERM, same as the steady-state probe path.
                if self._lock.is_locked() and not self._lock_still_held():
                    self._suicide_on_lock_loss()
                    return
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)

    def _become_leader_and_run(self) -> None:
        assert self._lock is not None

        if not self._lock.is_locked():
            logger.info(f'Acquiring the consolidation mode lock: {self._lock}')
            self._lock.acquire()
            logger.info('Consolidation mode lock acquired')

        # Touch signal file only after we hold the lock: standby
        # replicas blocked on acquire() must not leave it lying around
        # — update_managed_jobs_statuses early-returns when it exists.
        signal_file = pathlib.Path(
            constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
        signal_file.touch()
        try:
            managed_job_utils.ha_recovery_for_consolidation_mode()
        finally:
            signal_file.unlink(missing_ok=True)

        # Event-loop tick at events.EVENT_CHECKING_INTERVAL_SECONDS,
        # lock probe at _LOCK_PROBE_INTERVAL_SECONDS, sleep 1s between.
        refresh_event = events.ManagedJobEvent()
        now = time.monotonic()
        last_probe = now
        last_event = now - events.EVENT_CHECKING_INTERVAL_SECONDS
        while True:
            now = time.monotonic()
            if now - last_probe >= _LOCK_PROBE_INTERVAL_SECONDS:
                if not self._lock_still_held():
                    self._suicide_on_lock_loss()
                    return
                last_probe = now
            if now - last_event >= events.EVENT_CHECKING_INTERVAL_SECONDS:
                try:
                    refresh_event.run()
                except Exception:  # pylint: disable=broad-except
                    logger.exception('ManagedJobEvent tick failed; will retry')
                last_event = now
            time.sleep(1)

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
        # Re-touch the recovery signal file so no new controllers will be
        # started
        try:
            signal_file = pathlib.Path(
                constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
            signal_file.parent.mkdir(parents=True, exist_ok=True)
            signal_file.touch()
        except OSError:
            logger.warning('Failed to touch recovery signal file on lock-loss')
        # The lock is already released, kill job controllers to avoid split
        # brain, e.g. new job controllers might have been launched on the new
        # replica during rolling-update
        try:
            managed_job_scheduler.kill_local_job_controllers()
        except Exception:  # pylint: disable=broad-except
            logger.exception('Failed to kill local controllers on lock-loss')
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

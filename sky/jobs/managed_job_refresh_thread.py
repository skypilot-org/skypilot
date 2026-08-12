"""Run the managed-job-status-refresh loop as a thread in the API server."""
import pathlib
import signal
import threading
import time
import typing
from typing import Optional, Sequence

from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler as managed_job_scheduler
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants
from sky.skylet import events
from sky.utils import locks

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

_LOCK_PROBE_INTERVAL_SECONDS = 5
_ACQUIRE_RETRY_INTERVAL_SECONDS = 5

# Step-down controller drain. On losing the lock this replica must stop its own
# job controllers, and stepping down in place (rather than exiting) means there
# is no container teardown afterwards to reap whatever survives a SIGTERM — so
# we wait for them to actually exit and escalate to SIGKILL.
#
# The total budget must stay comfortably below
# _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS: that wait is what separates our dying
# controllers from the new leader's recovery sweep, and the sweep decides a job
# needs re-adopting by checking whether its controller pid is alive *locally*
# (see _drain_local_controllers).
_STEP_DOWN_SIGTERM_GRACE_SECONDS = 5
_STEP_DOWN_DRAIN_TIMEOUT_SECONDS = 10
_STEP_DOWN_DRAIN_POLL_SECONDS = 0.2

# How long to wait after acquiring the consolidation-mode lock before running
# recovery. During a rolling update the new leader blocks on acquire() while
# the old API server still holds the lock. The lock is released when the old
# main process exits, but that pod's job controllers are detached subprocesses
# (start_new_session=True), so they are not killed until the container itself
# is torn down a moment later. If recovery ran in that residual window, it would
# reset jobs that the still-alive (but about-to-die) old controllers can briefly
# re-claim, stamping their soon-dead PIDs back onto the jobs;
# update_managed_jobs_statuses would then mark those jobs FAILED_CONTROLLER (a
# split brain across the upgrade overlap). Waiting here lets the old container
# finish terminating before we reset and re-adopt its jobs. The recovery signal
# file stays in place during the wait, so no controllers are started and no job
# is marked FAILED_CONTROLLER in the meantime.
_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS = 15


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
                # Returns only after a step-down; the value says whether it is
                # safe to contend for leadership again. Looping is safe because
                # the step-down gated controller starts, confirmed our own
                # controllers are gone, and replaced the lock object — so the
                # next pass really does acquire() before it leads, instead of
                # running recovery on a stale local `_acquired` flag.
                if not self._become_leader_and_run():
                    logger.error(
                        'Not contending for the consolidation mode lock again; '
                        'this replica no longer runs managed-job recovery or '
                        'the refresh loop until it restarts')
                    return
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    'managed-job refresh error; '
                    f'retrying in {_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                # If we previously held the lock and lost the session
                # mid-recovery, retrying would run as a stale leader
                # (local `_acquired` flag still True, server-side lock
                # released, another replica can grab it). Step down first,
                # same as the steady-state probe path.
                if self._lock.is_locked() and not self._lock_still_held():
                    if not self._step_down_on_lock_loss():
                        logger.error(
                            'Not contending for the consolidation mode lock '
                            'again; this replica no longer runs managed-job '
                            'recovery or the refresh loop until it restarts')
                        return
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)

    def _become_leader_and_run(self) -> bool:
        """Take leadership, recover, then run the refresh loop.

        Returns only when this replica has lost the lock and stepped down. The
        return value is whether it is safe to contend for leadership again (see
        _step_down_on_lock_loss).
        """
        assert self._lock is not None

        # Touch the signal file BEFORE acquiring the lock: new controllers
        # must not be started until recovery has run. During a rolling
        # update we block on acquire() while the old API server still holds
        # the lock; if a controller were started on this replica in that
        # window, the old server's update_managed_jobs_statuses wouldn't see
        # its process and could mark the job FAILED_CONTROLLER. The signal
        # file makes update_managed_jobs_statuses and the scheduler's
        # controller-start path early-return until recovery completes.
        # NOTE: the acquire is deliberately NOT inside the try/finally that
        # wraps recovery below. The finally unlinks the signal file, but the
        # lock-loss step-down path (_step_down_on_lock_loss) re-touches it to
        # keep controllers gated for as long as we are not the leader — so that
        # path must not be followed by an unlink. Scoping the finally to
        # recovery only keeps those two concerns from fighting. It also means a
        # raise from acquire() leaves the gate file in place while run()
        # retries, which is what we want (controller starts stay gated until we
        # hold the lock).
        signal_file = pathlib.Path(
            constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
        signal_file.touch()

        if not self._lock.is_locked():
            logger.info(f'Acquiring the consolidation mode lock: {self._lock}')
            self._lock.acquire()
            logger.info('Consolidation mode lock acquired')

        # Wait before recovery so a prior leader (e.g. the old pod during a
        # rolling update) is fully gone first; see the comment on
        # _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS. The signal file touched above
        # stays in place, gating controller starts and the FAILED_CONTROLLER
        # sweep until recovery completes.
        logger.info(
            f'Waiting {_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS}s after acquiring '
            'the consolidation mode lock before running recovery, to let any '
            'previous leader finish shutting down')
        time.sleep(_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS)

        # The wait above widens the window between acquiring the lock and
        # running recovery, during which the lock's underlying session could go
        # silently stale (PostgresLock only). Re-verify we still hold the lock
        # before recovery; otherwise another replica may have taken it and
        # could be recovering concurrently, so step down rather than run a
        # second recovery loop. _step_down_on_lock_loss re-touches the signal
        # file to keep controllers gated, so leave the file in place here.
        if not self._lock_still_held():
            return self._step_down_on_lock_loss()

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
                    return self._step_down_on_lock_loss()
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

    @staticmethod
    def _controllers_gone(records: Sequence[
        managed_job_state.ControllerPidRecord], timeout: float) -> bool:
        """Poll until none of *records* is a live local process, or time out."""
        deadline = time.time() + timeout
        while True:
            alive = [
                r.pid
                for r in records
                if managed_job_utils.controller_process_alive(r)
            ]
            if not alive:
                return True
            if time.time() >= deadline:
                logger.warning(
                    f'Controllers still alive after {timeout}s: {alive}')
                return False
            time.sleep(_STEP_DOWN_DRAIN_POLL_SECONDS)

    def _drain_local_controllers(self) -> bool:
        """Stop this replica's job controllers and confirm they are gone.

        This is the anti-split-brain mechanism of a step-down, and now that the
        process no longer exits it is the *only* one. The new leader's recovery
        sweep decides a job needs re-adopting by checking whether its recorded
        controller pid is alive, and that check is a local ``psutil`` lookup
        (``controller_process_alive``) — so it can never see another replica's
        controllers and always concludes they are dead. If one of ours outlives
        our leadership, that job ends up with two controllers.

        Signalling is therefore not enough. Previously the process SIGTERMed
        itself here and the container teardown that followed reaped anything
        that ignored the signal (controllers are detached subprocesses, so the
        parent exiting does not kill them); stepping down in place removes that
        backstop. So: SIGTERM, wait for exit, escalate to SIGKILL, and report
        whether they are actually gone.

        Returns True iff no controller recorded for this replica is still alive.
        """
        records = managed_job_scheduler.get_controller_process_records()
        if records is None:
            # The pid file could not be read, so we can neither enumerate our
            # controllers nor confirm their death. Report failure rather than
            # assume there were none.
            logger.error(
                'Cannot read the controller pid file, so this replica cannot '
                'confirm its job controllers are gone')
            return False
        if not records:
            return True

        managed_job_scheduler.kill_local_job_controllers()
        if self._controllers_gone(records, _STEP_DOWN_SIGTERM_GRACE_SECONDS):
            return True

        logger.warning(
            f'Job controllers survived SIGTERM for '
            f'{_STEP_DOWN_SIGTERM_GRACE_SECONDS}s; escalating to SIGKILL')
        managed_job_scheduler.kill_local_job_controllers(signal.SIGKILL)
        return self._controllers_gone(
            records,
            _STEP_DOWN_DRAIN_TIMEOUT_SECONDS - _STEP_DOWN_SIGTERM_GRACE_SECONDS)

    def _step_down_on_lock_loss(self) -> bool:
        """Give up leadership without killing the API server process.

        The API server keeps serving; only the leader role is handed over. This
        replica gates controller starts, stops the controllers it owns, drops
        its lock object, and returns to contending for the lock as a follower.

        Returns True iff it is safe to contend for leadership again — i.e. we
        proved our own controllers are gone. If we could not, contending again
        risks two controllers on one job, so the caller must stop instead.
        """
        assert self._lock is not None
        logger.error(f'Lost consolidation mode lock {self._lock}; stepping '
                     'down to follower')

        # 1. Gate controller starts on this replica for as long as we are not
        #    the leader. _become_leader_and_run touches this again before its
        #    next acquire and only unlinks it once its own recovery completes.
        try:
            signal_file = pathlib.Path(
                constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
            signal_file.parent.mkdir(parents=True, exist_ok=True)
            signal_file.touch()
        except OSError:
            logger.warning('Failed to touch recovery signal file on lock-loss')

        # 2. Stop the controllers we own and confirm it. The lock is already
        #    released server-side, so a new leader can be recovering within
        #    milliseconds.
        try:
            drained = self._drain_local_controllers()
        except Exception:  # pylint: disable=broad-except
            logger.exception('Failed to drain local controllers on lock-loss')
            drained = False

        # 3. Replace the lock object. A release() whose connection is already
        #    dead leaves `_acquired` True — it is only cleared after a
        #    successful unlock — and is_locked() reads exactly that flag, so
        #    reusing this object would make the next _become_leader_and_run
        #    skip acquire() and run recovery believing it leads. Release
        #    best-effort first so the pooled connection is returned or
        #    invalidated rather than leaked, then start from a clean object.
        try:
            self._lock.release()
        except Exception:  # pylint: disable=broad-except
            logger.debug('Ignoring error while releasing the lost lock',
                         exc_info=True)
        self._lock = locks.get_lock(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)

        if not drained:
            logger.error(
                'Step-down could not confirm this replica\'s job controllers '
                'are gone; refusing to lead again. Controller starts stay '
                'gated on this replica, so its surviving controllers keep '
                'their jobs and no second controller is started here, but '
                'this replica needs a restart to become eligible again')
        else:
            logger.info('Stepped down cleanly; contending for the '
                        'consolidation mode lock again as a follower')
        return drained


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

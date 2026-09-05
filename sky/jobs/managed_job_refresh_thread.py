"""Run the managed-job-status-refresh loop as a thread in the API server."""
import os
import pathlib
import signal
import threading
import time
import typing
from typing import Optional, Sequence

from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler as managed_job_scheduler
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants
from sky.skylet import events
from sky.utils import locks

if typing.TYPE_CHECKING:
    from sky.jobs import state as managed_job_state

logger = sky_logging.init_logger(__name__)

_LOCK_PROBE_INTERVAL_SECONDS = 5
_ACQUIRE_RETRY_INTERVAL_SECONDS = 5

# Step-down controller drain. Stepping down in place leaves no container
# teardown to reap controllers that ignore SIGTERM, so wait for them to exit and
# escalate to SIGKILL. The budget (5s to detect + 3s + 3s) fits inside the new
# leader's 15s recovery wait on the steady-state path only: the two clocks start
# at different times, and the exception path can notice arbitrarily late. What
# correctness rests on is the drain confirming or admitting failure, not on that
# margin — see _step_down_on_lock_loss.
_STEP_DOWN_SIGTERM_GRACE_SECONDS = 3
_STEP_DOWN_SIGKILL_GRACE_SECONDS = 3
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

# Pin the margin above: retuning any one constant must not silently delete it
# (the drain tests zero the budgets, so they would not catch it).
assert (_LOCK_PROBE_INTERVAL_SECONDS + _STEP_DOWN_SIGTERM_GRACE_SECONDS +
        _STEP_DOWN_SIGKILL_GRACE_SECONDS < _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS
       ), ('a step-down must be able to detect the loss and drain before the '
           'new leader stops waiting and starts recovery')


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
                # Returns only after a step-down. True: it confirmed our
                # controllers are gone and replaced the lock object, so the next
                # pass really does acquire() before leading. False: it already
                # asked this process to exit, and logged why.
                if not self._become_leader_and_run():
                    return
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    'managed-job refresh error; '
                    f'retrying in {_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                # A stale leader (local `_acquired` still True, lock released
                # server-side) must not retry as leader; step down first, same
                # as the steady-state probe path.
                if self._lock.is_locked() and not self._lock_still_held():
                    if not self._step_down_on_lock_loss():
                        return
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)

    def _become_leader_and_run(self) -> bool:
        """Take leadership, recover, then run the refresh loop.

        Returns only when this replica has lost the lock and stepped down. The
        return value is whether it is safe to contend for leadership again (see
        _step_down_on_lock_loss).
        """
        assert self._lock is not None

        # Touch BEFORE acquiring: the file holds off the FAILED_CONTROLLER sweep
        # and has to cover the whole time we contend, since acquire() can block
        # for a long time during a rolling update. See step 1 of
        # _step_down_on_lock_loss for what it does and does not gate.
        # NOTE: acquire stays outside the try/finally below on purpose — the
        # finally unlinks this file while the step-down re-touches it, so
        # scoping the finally to recovery keeps the two from fighting, and a
        # raise from acquire() leaves the file in place while run() retries.
        signal_file = pathlib.Path(
            constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
        signal_file.touch()

        if not self._lock.is_locked():
            logger.info(f'Acquiring the consolidation mode lock: {self._lock}')
            self._lock.acquire()
            logger.info('Consolidation mode lock acquired')

        # Let a prior leader finish shutting down first; see
        # _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS. The signal file stays in place.
        logger.info(
            f'Waiting {_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS}s after acquiring '
            'the consolidation mode lock before running recovery, to let any '
            'previous leader finish shutting down')
        time.sleep(_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS)

        # The wait widens the window in which the session can go silently stale
        # (PostgresLock only), so re-verify before recovery: another replica may
        # hold the lock and be recovering by now. Leave the signal file in place
        # — the step-down re-touches it.
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
    def _controllers_gone(
            records: Sequence['managed_job_state.ControllerPidRecord'],
            timeout: float) -> bool:
        """Poll until none of *records* is a live local process, or time out."""
        deadline = time.monotonic() + timeout
        remaining = list(records)
        while True:
            # Poll only the survivors: the alive set can only shrink (liveness
            # matches on (pid, started_at)), and *records* is every pid this
            # replica ever recorded — the pid file is append-only in
            # consolidation mode, not the live pool.
            remaining = [
                r for r in remaining
                if managed_job_utils.controller_process_alive(r)
            ]
            if not remaining:
                return True
            if time.monotonic() >= deadline:
                logger.warning(f'Controllers still alive after {timeout}s: '
                               f'{[r.pid for r in remaining]}')
                return False
            time.sleep(_STEP_DOWN_DRAIN_POLL_SECONDS)

    def _drain_local_controllers(self) -> bool:
        """Stop this replica's job controllers and confirm they are gone.

        The anti-split-brain mechanism of a step-down, and now that the process
        no longer exits, the only one: the new leader decides a job needs
        re-adopting from a *local* psutil check (``controller_process_alive``),
        so it never sees another replica's controllers and always concludes they
        are dead — a survivor of ours means two controllers on one job.

        Signalling alone is therefore not enough, because stepping down in place
        removes the container teardown that used to reap controllers ignoring
        SIGTERM (they are detached subprocesses): SIGTERM, wait, escalate to
        SIGKILL, report. Assumes the recorded pid is the controller rather than
        its shell wrapper — bash execs the final simple command of a ``-c``
        script, and ``scheduler.py``'s ``run_cmd`` carries the matching note
        since that is where the assumption can be broken.

        TODO(aylei): make controller ownership DB-visible (the owning replica
        alongside job_info's controller_pid, so liveness can answer "not mine"
        instead of "dead") and this drain's fallback can go away.

        Returns True iff no controller recorded for this replica is still alive.
        """
        records = managed_job_scheduler.get_controller_process_records()
        if records is None:
            # Unreadable pid file: we can neither enumerate our controllers nor
            # confirm their death, so report failure rather than assume none.
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
        return self._controllers_gone(records, _STEP_DOWN_SIGKILL_GRACE_SECONDS)

    def _step_down_on_lock_loss(self) -> bool:
        """Give up leadership without killing the API server process.

        The server keeps serving; only the leader role moves. Holds off the
        FAILED_CONTROLLER sweep, stops the controllers this replica owns, drops
        the lock object, and returns to contending as a follower.

        Returns True iff it is safe to lead again, i.e. we proved our
        controllers are gone; otherwise it exits the process and returns False.
        """
        assert self._lock is not None
        logger.error(f'Lost consolidation mode lock {self._lock}; stepping '
                     'down to follower')

        # 1. Re-touch the recovery signal file: it holds off the
        #    FAILED_CONTROLLER sweep (update_managed_jobs_statuses in
        #    sky/jobs/utils.py, and job_lib) so the jobs whose controllers we
        #    are about to stop are not marked failed across the handoff. It does
        #    NOT gate controller starts — maybe_start_controllers never reads
        #    it; what stops a non-leader is its own unconditional early return.
        #    The effect can be fleet-wide rather than replica-local, since one
        #    HA chart configuration mounts the shared state volume at ~/.sky;
        #    the next leader's recovery unlinks it, which bounds the window.
        try:
            signal_file = pathlib.Path(
                constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
            signal_file.parent.mkdir(parents=True, exist_ok=True)
            signal_file.touch()
        except OSError:
            logger.warning('Failed to touch recovery signal file on lock-loss')

        # 2. Stop the controllers we own and confirm it — the lock is already
        #    released server-side, so a new leader can be recovering by now.
        try:
            drained = self._drain_local_controllers()
        except Exception:  # pylint: disable=broad-except
            logger.exception('Failed to drain local controllers on lock-loss')
            drained = False

        # 3. Replace the lock object: a release() on a dead connection leaves
        #    `_acquired` True (only cleared after a successful unlock) and
        #    is_locked() reads exactly that flag, so reusing it would make the
        #    next pass skip acquire() and run recovery believing it leads.
        #    Release best-effort first so the pooled connection is not leaked.
        #    Same discipline as leader_election.AdvisoryLockElector, which this
        #    cannot use yet: its try_acquire is non-blocking, and the blocking
        #    acquire() above is load-bearing for the rolling-update handoff.
        try:
            self._lock.release()
        except Exception:  # pylint: disable=broad-except
            logger.debug('Ignoring error while releasing the lost lock',
                         exc_info=True)
        self._lock = locks.get_lock(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)

        if not drained:
            # Last resort: exit so the container teardown reaps the survivors,
            # which is what closed this hole before. Declining to lead is not
            # equivalent (see _drain_local_controllers), and with the chart's
            # default of one replica it would leave nobody running recovery at
            # all, with nothing to restart it.
            logger.error(
                'Step-down could not confirm this replica\'s job controllers '
                'are gone; exiting so the orchestrator restarts this replica '
                'and its teardown reaps them')
            os.kill(os.getpid(), signal.SIGTERM)
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

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

# Step-down controller drain. On losing the lock this replica must stop its own
# job controllers, and stepping down in place (rather than exiting) means there
# is no container teardown afterwards to reap whatever survives a SIGTERM — so
# we wait for them to actually exit and escalate to SIGKILL.
#
# How this races the new leader's recovery sweep, precisely, because the two
# clocks do NOT start together: the new leader's
# _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS (15s) starts when it ACQUIRES the lock,
# which it can do the moment our session dies server-side. Our drain starts only
# once we NOTICE, which on the steady-state path is up to
# _LOCK_PROBE_INTERVAL_SECONDS (5s) later, and on the exception path (a raise
# out of _become_leader_and_run) can be arbitrarily later. So the budget below
# buys margin on the steady-state path only:
#
#     5s detection + (3s SIGTERM + 3s SIGKILL) drain = 11s  <  15s recovery wait
#
# It does not make the exception path safe, and it is not the thing the
# correctness of the step-down rests on. What that rests on is the drain either
# confirming the controllers are gone or admitting it could not — see
# _step_down_on_lock_loss, which falls back to exiting the process in the second
# case rather than assuming the wait covered us.
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

# Pin the margin the step-down comment above reasons about, so retuning any one
# of these three constants cannot silently delete it. The drain tests
# monkeypatch the budgets to 0, so they would not catch it either.
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
                # Returns only after a step-down; the value says whether it is
                # safe to contend for leadership again. Looping on True is safe
                # because the step-down confirmed our own controllers are gone
                # and replaced the lock object — so the next pass really does
                # acquire() before it leads, instead of running recovery on a
                # stale local `_acquired` flag. False means the step-down
                # already asked this process to exit (it logs why), so stop the
                # thread and let the shutdown proceed.
                if not self._become_leader_and_run():
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
                        return
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)

    def _become_leader_and_run(self) -> bool:
        """Take leadership, recover, then run the refresh loop.

        Returns only when this replica has lost the lock and stepped down. The
        return value is whether it is safe to contend for leadership again (see
        _step_down_on_lock_loss).
        """
        assert self._lock is not None

        # Touch the signal file BEFORE acquiring the lock: it holds off the
        # FAILED_CONTROLLER sweep (update_managed_jobs_statuses in
        # sky/jobs/utils.py, and job_lib), and that has to be in place for the
        # whole time we are contending, not just once we win. During a rolling
        # update we block on acquire() while the old API server still holds the
        # lock; jobs whose controllers are moving between replicas must not be
        # marked FAILED_CONTROLLER for it in that window. See step 1 of
        # _step_down_on_lock_loss for what the file does and does not gate.
        # NOTE: the acquire is deliberately NOT inside the try/finally that
        # wraps recovery below. The finally unlinks the signal file, but the
        # lock-loss step-down path (_step_down_on_lock_loss) re-touches it to
        # hold off the sweep for as long as we are not the leader — so that
        # path must not be followed by an unlink. Scoping the finally to
        # recovery only keeps those two concerns from fighting. It also means a
        # raise from acquire() leaves the file in place while run() retries,
        # which is what we want.
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
        # stays in place, holding off the FAILED_CONTROLLER sweep until recovery
        # completes.
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
        # file, so leave the file in place here.
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
            # Poll only the survivors. The alive set can only shrink —
            # controller_process_alive matches on (pid, started_at), so a record
            # that is gone cannot come back under a recycled pid — and *records*
            # is every pid this replica ever recorded, not the live pool: the
            # pid file is append-only and nothing truncates it in consolidation
            # mode.
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

        This is the anti-split-brain mechanism of a step-down, and now that the
        process no longer exits it is the *only* one. The new leader's recovery
        sweep decides a job needs re-adopting by checking whether its recorded
        controller pid is alive, and that check is a local ``psutil`` lookup
        (``controller_process_alive``) — so it can never see another replica's
        controllers and always concludes they are dead. If one of ours outlives
        our leadership, that job ends up with two controllers.

        TODO(aylei): this is a local compensation for a fact that is missing
        from the shared state, and it is the second one —
        maybe_start_controllers already refuses to start controllers off the
        request path for the same reason. The deeper fix is to make controller
        ownership DB-visible (e.g.
        record the owning replica alongside job_info's controller_pid, so
        controller_process_alive can answer "not mine" instead of "dead"), which
        would also let the fallback below go away. Out of scope here: schema
        change plus every controller_process_alive call site.

        Signalling is therefore not enough. Previously the process SIGTERMed
        itself here and the container teardown that followed reaped anything
        that ignored the signal (controllers are detached subprocesses, so the
        parent exiting does not kill them); stepping down in place removes that
        backstop. So: SIGTERM, wait for exit, escalate to SIGKILL, and report
        whether they are actually gone.

        Load-bearing assumption, recorded because it is easy to break from far
        away: the recorded pid must be the controller itself, not a shell
        wrapper. ``launch_new_process_tree`` runs ``nohup bash -c '<export>;
        <activate>; python -m sky.jobs.controller'`` and records ``$!``, i.e.
        the bash pid — but bash execs the final simple command of a ``-c``
        script, so that pid becomes the controller. Verified live: a
        cmdline-based process count over the leader's pod showed exactly one
        process per controller (a surviving wrapper would have doubled it) and
        dropped to zero after this drain. ``scheduler.py``'s ``run_cmd`` carries
        a matching note, since that is where the assumption can be broken.

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
        return self._controllers_gone(records, _STEP_DOWN_SIGKILL_GRACE_SECONDS)

    def _step_down_on_lock_loss(self) -> bool:
        """Give up leadership without killing the API server process.

        The API server keeps serving; only the leader role is handed over. This
        replica holds off the FAILED_CONTROLLER sweep, stops the controllers it
        owns, drops its lock object, and returns to contending as a follower.

        Returns True iff it is safe to contend for leadership again — i.e. we
        proved our own controllers are gone. If we could not, this exits the
        process (see below) and returns False so the caller stops the thread.
        """
        assert self._lock is not None
        logger.error(f'Lost consolidation mode lock {self._lock}; stepping '
                     'down to follower')

        # 1. Re-touch the recovery signal file, kept for what it actually does
        #    rather than what the surrounding comments have long claimed. It is
        #    consulted by update_managed_jobs_statuses (sky/jobs/utils.py) and
        #    job_lib, NOT by scheduler.maybe_start_controllers — so it does not
        #    itself gate controller starts. What keeps a non-leader from
        #    starting controllers in consolidation mode is
        #    maybe_start_controllers' unconditional early return on the request
        #    path; the leader's recovery is the only starter. The file's job
        #    here is to hold off the FAILED_CONTROLLER sweep across the
        #    handoff, so jobs whose controllers we are about to stop are not
        #    marked failed for it.
        #    Note it can be fleet-wide, not replica-local: the HA chart has a
        #    configuration that mounts the shared state volume at ~/.sky, and
        #    this path lives directly under it. The next leader's recovery
        #    unlinks it (in a finally), which bounds the window.
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
        #    Same discipline as AdvisoryLockElector.release() + try_acquire() in
        #    sky/utils/leader_election.py; this loop cannot use
        #    that elector yet because its try_acquire is non-blocking and the
        #    blocking acquire() above is load-bearing for the rolling-update
        #    handoff. Keep the two in sync until they are merged.
        try:
            self._lock.release()
        except Exception:  # pylint: disable=broad-except
            logger.debug('Ignoring error while releasing the lost lock',
                         exc_info=True)
        self._lock = locks.get_lock(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)

        if not drained:
            # Last resort: exit, so the container teardown reaps the survivors —
            # which is what actually closed this hole before this change.
            # Declining to lead instead is not equivalent, because another
            # replica cannot see our controllers and re-adopts their jobs
            # regardless; see _drain_local_controllers.
            #
            # Exiting also covers the single-replica case (the chart's default
            # apiService.replicas is 1): there, stopping this thread would leave
            # nobody running recovery, controller top-ups or the
            # FAILED_CONTROLLER sweep, with nothing to restart it.
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

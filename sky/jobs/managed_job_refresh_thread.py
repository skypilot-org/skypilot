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
from sky.utils import leader_election

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

_ROLE_RENEW_INTERVAL_SECONDS = 5
_ACQUIRE_RETRY_INTERVAL_SECONDS = 5

# Lease timing for the consolidation role, deliberately looser than the
# `leader_election` defaults (ttl 60 / interval 10 / deadline 30). Both loose
# knobs buy the same thing -- more waiting, fewer chances of two leaders --
# which is the right trade for this role and the wrong one for the idempotent
# per-tick daemons those defaults are sized for:
#
#   - ttl bounds how long a follower must wait before it may take a lease whose
#     holder stopped renewing, i.e. the failover latency after a hard crash.
#     This leader owns the job-controller process group, so a second leader does
#     not merely repeat a tick: it restarts controllers for jobs whose
#     controllers may still be running elsewhere, leaving two controllers
#     driving one job. Waiting longer is strictly preferable to racing.
#   - renew_deadline is how long a leader keeps trying before it concludes the
#     role is gone. Here that conclusion is a suicide timer, not a step-down:
#     losing the role means SIGTERMing this API server so its detached
#     controllers go down with it (see `_suicide_on_role_loss`). A server
#     restart costs far more than the leaderless gap it prevents, so the
#     deadline is sized to ride out a database failover or restart (tens of
#     seconds) rather than to react to one.
#
# The renew cadence stays at the 5s this daemon has always probed its lock at,
# so the lease gets ~17 attempts inside its deadline. Advisory keeps the same
# cadence but is not quite byte-identical any more: a step-down is noticed by
# the renewer (within one cadence) and then by the loop (within a second)
# rather than by one inline probe, and a follower re-bids every
# `_ACQUIRE_RETRY_INTERVAL_SECONDS` instead of the blocking acquire's 1s poll.
# Both are dominated by `_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS`.
#
# The margin (ttl - deadline) is what a stepping-down leader has to get its
# controllers dead before any other replica may adopt their jobs, on top of the
# `_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS` the new leader waits anyway. Delivering
# the signals is a handful of `os.kill` calls, so the margin is really there to
# absorb a stop-the-world pause.
#
# Cost of the loose ttl, stated plainly: a leader that exits *without*
# releasing -- a crash, or a pod that is simply gone -- holds the role for the
# rest of the ttl, so that handoff is slower than the advisory lock's, which
# came free as soon as the old main process exited. That is the same trade as
# above, and the gap is not job-visible: the FAILED_CONTROLLER sweep only runs
# inside a leader's own tick, and the recovery signal file is touched *before*
# the acquire, so a contending replica starts no controllers in the meantime.
_LEASE_TTL_SECONDS = 150.0
_RENEW_DEADLINE_SECONDS = 90.0

# How long to wait after acquiring the consolidation-mode role before running
# recovery. During a rolling update the new leader contends while the old API
# server still holds the role. The role is released when the old main process
# exits, but that pod's job controllers are detached subprocesses
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
        self._elector: Optional[leader_election.LeaderElector] = None
        self._renewer: Optional[leader_election.LeadershipRenewer] = None

    def run(self) -> None:
        # Elect through `sky.utils.leader_election` so this role honours the
        # same `SKYPILOT_LEADER_ELECTION_BACKEND` switch as every other
        # fleet-wide singleton: `advisory` (the default) is the historical
        # Postgres session-scoped advisory lock on the same lock id, so old and
        # new pods still contend together across a rolling upgrade; `lease` is
        # a renewable `leader_leases` row, which pins no connection for the
        # leadership term and carries an expiry the holder must keep extending.
        # The timing is this role's own (see `_LEASE_TTL_SECONDS`) rather than
        # the module defaults.
        self._elector = leader_election.get_elector(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID,
            ttl_seconds=_LEASE_TTL_SECONDS,
            renew_interval_seconds=_ROLE_RENEW_INTERVAL_SECONDS,
            renew_deadline_seconds=_RENEW_DEADLINE_SECONDS)

        while True:
            try:
                self._become_leader_and_run()
                # _become_leader_and_run only returns normally after
                # _suicide_on_role_loss sent SIGTERM. Re-entering would
                # re-contend for a role we just gave up on, touch the signal
                # file, and call ha_recovery -> maybe_start_controllers --
                # spawning fresh controllers while the new leader on another
                # replica is doing the same. Stop the thread instead so the
                # SIGTERM-driven drain runs to completion without further
                # controller churn.
                return
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    'managed-job refresh error; '
                    f'retrying in {_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                # If we were leading and lost the role mid-recovery, retrying
                # would run as a stale leader (our renewer has stopped, another
                # replica can take over). Hand off via SIGTERM, same as the
                # steady-state probe path. Still holding it, or never having
                # held it, is an ordinary retry.
                if self._renewer is not None and not self._renewer.holding:
                    self._suicide_on_role_loss()
                    return
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)

    def _become_leader_and_run(self) -> None:
        assert self._elector is not None

        # Touch the signal file BEFORE acquiring the role: new controllers
        # must not be started until recovery has run. During a rolling
        # update we contend while the old API server still holds the role;
        # if a controller were started on this replica in that window, the
        # old server's update_managed_jobs_statuses wouldn't see its process
        # and could mark the job FAILED_CONTROLLER. The signal file makes
        # update_managed_jobs_statuses and the scheduler's controller-start
        # path early-return until recovery completes.
        # NOTE: the acquire is deliberately NOT inside the try/finally that
        # wraps recovery below. The finally unlinks the signal file, but the
        # role-loss step-down path (_suicide_on_role_loss) re-touches it to
        # keep controllers gated through the shutdown drain — so that path must
        # not be followed by an unlink. Scoping the finally to recovery only
        # keeps those two concerns from fighting. It also means a raise from
        # the acquire leaves the gate file in place while run() retries, which
        # is what we want (controller starts stay gated until we hold the role).
        signal_file = pathlib.Path(
            constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
        signal_file.touch()

        if self._renewer is None:
            self._acquire_role()
        assert self._renewer is not None

        # Wait before recovery so a prior leader (e.g. the old pod during a
        # rolling update) is fully gone first; see the comment on
        # _RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS. The signal file touched above
        # stays in place, gating controller starts and the FAILED_CONTROLLER
        # sweep until recovery completes.
        logger.info(
            f'Waiting {_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS}s after acquiring '
            'the consolidation mode role before running recovery, to let any '
            'previous leader finish shutting down')
        time.sleep(_RECOVERY_WAIT_AFTER_ACQUIRE_SECONDS)

        # The wait above widens the window between acquiring the role and
        # running recovery, during which the role could be lost underneath us —
        # an advisory lock's session going silently stale, or a lease we stopped
        # being able to renew. Re-verify before recovery; otherwise another
        # replica may have taken it and could be recovering concurrently, so
        # step down rather than run a second recovery loop.
        # _suicide_on_role_loss re-touches the signal file and SIGTERMs the
        # process, so leave the file in place here.
        if not self._renewer.holding:
            self._suicide_on_role_loss()
            return

        try:
            managed_job_utils.ha_recovery_for_consolidation_mode()
        finally:
            signal_file.unlink(missing_ok=True)

        # Event-loop tick at events.EVENT_CHECKING_INTERVAL_SECONDS, role check
        # every pass, sleep 1s between.
        #
        # The role is checked on every pass rather than on its own slower
        # interval because `holding` is now an in-memory read -- the renewer
        # owns the round trip. Historically this probe *was* the round trip
        # (`is_session_alive()` per probe), which is why it was rate-limited to
        # `_ROLE_RENEW_INTERVAL_SECONDS`; keeping that gate now would only add
        # latency to the step-down for no saving. And that latency is not free:
        # the point of stepping down is to get this replica's controllers killed
        # before anyone else can adopt their jobs, so every second of detection
        # lag is spent out of the `ttl - deadline` margin.
        refresh_event = events.ManagedJobEvent()
        now = time.monotonic()
        last_event = now - events.EVENT_CHECKING_INTERVAL_SECONDS
        while True:
            now = time.monotonic()
            # Checked before the first tick too: recovery above walks every
            # managed job, so it can have outlasted the role.
            if not self._renewer.holding:
                self._suicide_on_role_loss()
                return
            if now - last_event >= events.EVENT_CHECKING_INTERVAL_SECONDS:
                try:
                    refresh_event.run()
                except Exception:  # pylint: disable=broad-except
                    logger.exception('ManagedJobEvent tick failed; will retry')
                last_event = now
            time.sleep(1)

    def _acquire_role(self) -> None:
        """Contend until this replica wins the role, then keep it renewed.

        Replaces the historical blocking `lock.acquire()`. The elector's bid is
        non-blocking, so a bid that raises (a database blip) is retried on the
        next pass instead of killing this thread for the lifetime of the pod —
        the old `acquire()` sat outside the retry loop's try.
        """
        assert self._elector is not None
        logger.info('Contending for the consolidation mode role: '
                    f'{self._elector.lock_id}')
        while True:
            try:
                acquired = self._elector.try_acquire()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Consolidation mode role bid failed: {e}; retrying in '
                    f'{_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                acquired = False
            if acquired:
                break
            time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)
        logger.info('Consolidation mode role acquired')
        # Renew on its own thread, not between the steps below. The wait before
        # recovery, recovery itself (it walks every managed job) and each
        # ManagedJobEvent tick are all long blocking calls, and a lease is only
        # as alive as its last renew: renewing between steps would let a slow
        # recovery lapse the lease and hand the role to another replica *while
        # recovery is running*, which is the worst possible moment. The thread
        # keeps the role alive throughout and this daemon just polls `holding`.
        # Published only once the thread is actually running: a `start()` that
        # raises (thread exhaustion is a real failure mode) would otherwise
        # leave a renewer that never renews, and on the advisory backend --
        # which has no deadline to catch it -- `holding` would stay True
        # forever, so we would run as leader with nothing probing the role.
        # Leaving it None instead makes the retry in `run()` bid again.
        renewer = leader_election.LeadershipRenewer(self._elector)
        renewer.start()
        self._renewer = renewer

    def _suicide_on_role_loss(self) -> None:
        """SIGTERM the API server process so the pod can restart cleanly."""
        assert self._elector is not None
        logger.error(
            f'Lost the consolidation mode role {self._elector.lock_id}; '
            'sending SIGTERM to the API server to step down')
        # Stop renewing first, so nothing re-establishes a role this process has
        # already decided to give up.
        #
        # Deliberately no `elector.release()` here. Releasing would let a
        # follower take the role immediately, which is exactly what must not
        # happen: the controllers killed below have to be gone before another
        # replica adopts their jobs. Sitting out the rest of the lease's
        # `ttl - renew_deadline` margin is the guarantee, not an oversight. (On
        # the advisory backend there is nothing to release anyway — the role is
        # already gone, which is why we are here.)
        if self._renewer is not None:
            self._renewer.stop()
            self._renewer = None
        # Re-touch the recovery signal file so no new controllers will be
        # started
        try:
            signal_file = pathlib.Path(
                constants.PERSISTENT_RUN_RESTARTING_SIGNAL_FILE).expanduser()
            signal_file.parent.mkdir(parents=True, exist_ok=True)
            signal_file.touch()
        except OSError:
            logger.warning('Failed to touch recovery signal file on role loss')
        # The role is gone, kill job controllers to avoid split brain, e.g. new
        # job controllers might have been launched on the new replica during
        # rolling-update
        try:
            managed_job_scheduler.kill_local_job_controllers()
        except Exception:  # pylint: disable=broad-except
            logger.exception('Failed to kill local controllers on role loss')
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

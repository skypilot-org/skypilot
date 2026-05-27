"""Run the managed-job-status-refresh loop as a thread in the API server.

Background
==========

The managed-job-status-refresh daemon owns two responsibilities in
consolidation mode:

1. Run ``ha_recovery_for_consolidation_mode`` on becoming leader, which
   restarts in-flight controllers for jobs whose controller PID is no
   longer alive.
2. Periodically run ``ManagedJobEvent`` to refresh job status across the
   fleet.

Historically the daemon was scheduled as an internal request on the shared
executor task queue, which means in HA mode it could move freely between
replicas: any executor worker that dequeued it became the leader.  The
``consolidation_mode_lock`` (a postgres advisory lock) was the only thing
gating concurrent execution.

In practice this produced a class of bug where two replicas concurrently
ran controllers for the same managed job.  The trigger could be as
innocuous as a ``BrokenProcessPool`` on the worker holding the daemon:
the executor and ``Pass D`` of the HA failover loop both re-enqueued the
task, the next dequeue landed on a different replica, the new leader's
``ha_recovery_for_consolidation_mode`` ran ``controller_process_alive`` on
the *old* leader's PIDs in its own PID namespace (where they don't
exist), declared all those controllers dead, and spawned a fresh pool of
controllers in parallel with the still-alive ones.

Design
======

Bind the daemon's lifecycle to the API server main process by running it
as a thread.  Now the daemon, the lock it holds, and the controllers it
spawns all live and die together with one OS process (and with one pod,
since the container PID namespace tears down on main exit).  The
cross-replica controller orphan can no longer happen as long as one
invariant holds: while this thread is alive and believes it is leader,
its postgres advisory lock is genuinely held server-side.

The advisory lock can in principle be lost while the main process is
alive — RDS maintenance restarts, NLB idle timeouts,
``idle_in_transaction_session_timeout``, ``pg_terminate_backend``,
transient network drops — and the lock's ``_acquired`` flag will stay
``True`` locally because nothing in the daemon's normal codepath
exercises the lock connection.  To recover the invariant, every tick we
probe the lock's session via ``PostgresLock.is_session_alive``; on
failure we ``SIGTERM`` the API server so K8s restarts the pod and the
remaining replicas re-elect a leader.

Catch-all on each event tick is kept (mirroring the old
``InternalRequestDaemon.run_event`` semantics): a transient exception
during ``event.run()`` should not take down the pod.  Only a confirmed
lock loss does.
"""
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

if typing.TYPE_CHECKING:
    pass

logger = sky_logging.init_logger(__name__)

# Probe the lock's PG session this often.  Short enough to react within
# seconds of a silent drop, long enough to be cheap; this is the bound
# on how long a stale leader can keep doing work after lock loss.
_LOCK_PROBE_INTERVAL_SECONDS = 5

# When the outer (acquire) loop fails — another replica still holds the
# lock, or our acquire connection died mid-wait — wait this long before
# retrying.  PostgresLock.acquire already polls internally at 1s, so
# this is just a safety sleep when its acquire raises.
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

    # ------------------------------------------------------------------ #
    # Thread entrypoint
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        # pylint: disable=import-outside-toplevel
        from sky.jobs import constants as managed_job_constants

        # Build the lock object in this thread so any PG connectivity
        # error at construction time surfaces here rather than at import.
        self._lock = locks.get_lock(
            managed_job_constants.CONSOLIDATION_MODE_LOCK_ID)

        while True:
            try:
                self._become_leader_and_run()
            except SystemExit:
                # _become_leader_and_run uses os.kill(SIGTERM) — let the
                # process tear down without an outer retry.
                raise
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    'managed-job refresh thread crashed outside the inner '
                    f'loop; retrying in {_ACQUIRE_RETRY_INTERVAL_SECONDS}s')
                time.sleep(_ACQUIRE_RETRY_INTERVAL_SECONDS)

    # ------------------------------------------------------------------ #
    # Leader path: acquire lock, run recovery, then loop event ticks
    # ------------------------------------------------------------------ #

    def _become_leader_and_run(self) -> None:
        # pylint: disable=import-outside-toplevel
        from sky.jobs import utils as managed_job_utils
        from sky.skylet import events

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

    # ------------------------------------------------------------------ #
    # Lock liveness
    # ------------------------------------------------------------------ #

    def _lock_still_held(self) -> bool:
        """True iff we are confident this replica still owns the lock."""
        assert self._lock is not None
        if isinstance(self._lock, locks.PostgresLock):
            return self._lock.is_session_alive()
        # FileLock and any other DistributedLock implementation: the
        # "session" concept doesn't apply (the lock is grounded in the
        # filesystem, which doesn't suffer silent loss the way a TCP
        # session does).  Trust the local flag.
        return self._lock.is_locked()

    def _suicide_on_lock_loss(self) -> None:
        """SIGTERM the API server process so the pod can restart cleanly."""
        logger.error(
            f'Lost consolidation mode lock {self._lock}; sending SIGTERM '
            'to the API server so the pod can be restarted and the '
            'leader re-elected on a sibling replica.')
        # SIGTERM rather than os._exit so uvicorn's lifespan shutdown
        # runs (atexit handlers, log flush, etc.).  K8s will restart
        # the container regardless.
        os.kill(os.getpid(), signal.SIGTERM)


def start_managed_job_refresh_daemon() -> None:
    """Start the refresh thread for this API server process, if needed.

    No-op when consolidation mode is off — mirrors the gating that the
    historical ``should_skip_managed_job_status_refresh`` provided.
    """
    # pylint: disable=import-outside-toplevel
    from sky.jobs import utils as managed_job_utils
    if not managed_job_utils.is_consolidation_mode():
        logger.debug('Consolidation mode is off; not starting the managed-job '
                     'refresh thread.')
        return
    logger.info('Starting the managed-job refresh thread')
    ManagedJobRefreshDaemonThread().start()

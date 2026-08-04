"""Leader election for singleton background work.

This module abstracts leader election behind :class:`LeaderElector` and offers
a second, lease-based backend. A *lease* is a single row
(``leader_leases(lock_id, holder, epoch, expires_at)``) refreshed by a short
``UPDATE`` in its own bounded transaction. Each renewal is a self-contained
transaction, so it can ride a pooled/pgBouncer connection and holds no
persistent connection between renewals; the row
also carries a monotonic ``epoch`` that acts as a fencing token, letting a
caller verify leadership atomically inside its own write transaction.

The backend is chosen by the ``SKYPILOT_LEADER_ELECTION_BACKEND`` environment
variable (``advisory`` -- the default -- or ``lease``) so the lease path is
opt-in and instantly revertible. On SQLite (single-node) there is no fleet to
coordinate, and both backends fall back to the local advisory/file lock path.
"""
import abc
import logging
import os
import socket
from typing import Optional
import uuid

import sqlalchemy

from sky import global_user_state
from sky.utils import locks
from sky.utils.db import db_utils

logger = logging.getLogger(__name__)

# Environment variable selecting the fleet-wide leader-election backend.
ENV_VAR_BACKEND = 'SKYPILOT_LEADER_ELECTION_BACKEND'
BACKEND_ADVISORY = 'advisory'
BACKEND_LEASE = 'lease'

# Default lease timing. These are three independent knobs (not derived from one
# another) so the retry budget and the safety margin can be tuned separately:
#   - ttl: a follower can grab the lease ``ttl`` after the leader's last
#     successful renew; this bounds hard-crash failover latency (a leader that
#     dies without releasing holds the lease for the full ttl).
#   - interval: how often the leader renews while healthy.
#   - deadline: the leader stops acting if it has not renewed within this long.
# The single-leader invariant is ``deadline < ttl`` (strict): the old leader
# steps down at ``deadline`` while a follower cannot acquire until ``ttl``, so
# the margin ``ttl - deadline`` (30s) absorbs the check->act gap -- clock
# jitter, a stop-the-world pause, or a VM freeze. The retry budget
# ``deadline - interval`` (20s) is how long a leader rides out a slow/failing DB
# before surrendering, and is kept at several times
# ``_RENEW_STATEMENT_TIMEOUT_MS`` so a single hung renew cannot eat the budget.
DEFAULT_LEASE_TTL_SECONDS = 60.0
DEFAULT_RENEW_INTERVAL_SECONDS = 10.0
DEFAULT_RENEW_DEADLINE_SECONDS = 30.0

# Server-side bound on a single renew/acquire/release. It caps *server-side*
# execution (a slow query, lock contention), so a locked/slow DB fails the call
# fast — the caller then steps down / re-contends. It does NOT bound a client
# socket that blackholes (the server never responds), which is instead governed
# by the engine's TCP-level timeouts. Keep it well under the retry budget
# (``renew_deadline - renew_interval``) so a single hung renew still leaves room
# for a clean retry before the deadline. A timed-out call raises and is treated
# as a lost role, which is the safe outcome.
_RENEW_STATEMENT_TIMEOUT_MS = 5000

# Guard the shipped defaults: the retry budget (deadline - interval) must stay
# above a single statement_timeout so one hung renew cannot consume the whole
# budget, and the full ordering must hold. Instances may override the timing
# (e.g. tests using short TTLs) and are checked for ordering in the constructor;
# this pins the defaults we actually ship.
assert (_RENEW_STATEMENT_TIMEOUT_MS / 1000 < DEFAULT_RENEW_INTERVAL_SECONDS <
        DEFAULT_RENEW_DEADLINE_SECONDS < DEFAULT_LEASE_TTL_SECONDS), (
            'default lease timing violates statement_timeout < interval < '
            'deadline < ttl')

# Name of the lease table (see the ``leader_leases`` migration in the state DB).
_LEASE_TABLE = 'leader_leases'

# A stable, process-unique holder identity. Leadership is per-process (the
# daemon is gated to one runner per pod), so hostname + pid + a random suffix
# uniquely and durably names this candidate for its whole lifetime. The random
# suffix guards against pid reuse racing a not-yet-expired lease row.
_HOLDER_ID = f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}'


class LeaderElector(abc.ABC):
    """A single-winner election for one ``lock_id`` across the fleet.

    Lifecycle: call :meth:`try_acquire` to bid for leadership; while leading,
    call :meth:`renew` periodically to confirm/extend the role (a ``False``
    return means the role was lost and the caller must stop leader work); call
    :meth:`release` to step down cleanly.
    """

    def __init__(self, lock_id: str):
        self._lock_id = lock_id

    @abc.abstractmethod
    def try_acquire(self) -> bool:
        """Non-blocking bid for leadership. True iff this candidate leads."""
        raise NotImplementedError

    @abc.abstractmethod
    def renew(self) -> bool:
        """Confirm/extend leadership. False iff the role has been lost."""
        raise NotImplementedError

    @abc.abstractmethod
    def release(self) -> None:
        """Step down cleanly. Safe to call when not leading."""
        raise NotImplementedError

    def fencing_token(self) -> Optional[int]:
        """A monotonic token for the current leadership term, if any.

        Backends that can hand out a fencing token (the lease backend) return a
        strictly increasing integer per acquisition, so a caller can stamp or
        guard its writes and have a resource reject a stale leader. Backends
        without one (advisory locks) return ``None``.
        """
        return None

    @property
    def renew_interval_seconds(self) -> float:
        """How often :meth:`renew` should be called while leading."""
        return DEFAULT_RENEW_INTERVAL_SECONDS

    @property
    def renew_deadline_seconds(self) -> Optional[float]:
        """Step down if no renew has succeeded within this many seconds.

        A time-based backend (the lease) returns a finite deadline so the runner
        stops acting once its lease is stale, even if a renew is hanging or the
        process was paused across a renew. A backend whose leadership is not
        time-bounded (the advisory lock, whose lock is held until the session
        dies) returns ``None`` -- there is no deadline to enforce, matching its
        historical behavior of stepping down only on a failed liveness probe.
        """
        return None


class AdvisoryLockElector(LeaderElector):
    """Leader election backed by a Postgres session-scoped advisory lock.

    Wraps ``sky.utils.locks`` with no behavior change from the historical path:
    ``try_acquire`` is a non-blocking advisory-lock acquire, ``renew`` is the
    session-liveness probe, and ``release`` drops the lock. Provides no fencing
    token. On non-Postgres backends the underlying ``FileLock`` makes this a
    process-local lock, which is exactly right for single-node deployments.
    """

    def __init__(self, lock_id: str):
        super().__init__(lock_id)
        self._lock: Optional[locks.DistributedLock] = None

    def try_acquire(self) -> bool:
        # Build a fresh lock object each bid so a prior term's closed connection
        # is never reused (mirrors the historical per-cycle ``get_lock`` call).
        lock = locks.get_lock(self._lock_id)
        try:
            lock.acquire(blocking=False)
        except locks.LockTimeout:
            return False
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('%s: advisory acquire failed: %s', self._lock_id, e)
            return False
        self._lock = lock
        return True

    def renew(self) -> bool:
        if self._lock is None:
            return False
        # Only a Postgres session can be silently reaped; a file lock is held as
        # long as this process lives.
        if isinstance(self._lock, locks.PostgresLock):
            return self._lock.is_session_alive()
        return True

    def release(self) -> None:
        if self._lock is None:
            return
        try:
            self._lock.release()
        except Exception as e:  # pylint: disable=broad-except
            # Releasing an already-dead session can raise; the role is gone
            # regardless, so downgrade to a debug line.
            logger.debug('%s: advisory release failed: %s', self._lock_id, e)
        finally:
            self._lock = None


class PgLeaseElector(LeaderElector):
    """Leader election backed by a renewable Postgres lease row.

    ``try_acquire`` is an insert-or-take-over upsert that bumps ``epoch`` on
    every fresh acquisition; ``renew`` is a separate statement that only extends
    a lease we still validly hold, keeping ``epoch`` fixed for the term. Every
    call is one short bounded transaction (see :meth:`_execute_bounded`) on the
    pooled engine, so it rides a transaction-mode pooler and holds no persistent
    connection between renewals.
    """

    # Insert-or-take-over in one statement, used only by ``try_acquire``. All
    # time math is server-side (``now()``) so replicas never compare against
    # their own wall clocks. ``RETURNING`` yields a row iff we hold the lease
    # after the statement: on first insert, on a redundant acquire while we
    # still hold it, or on takeover of an expired lease. When another holder's
    # lease is still valid the ``DO UPDATE`` WHERE is false, no row is written,
    # and ``RETURNING`` is empty. ``epoch`` is kept only when we already hold a
    # *still-valid* lease (a redundant acquire); every other write is a fresh
    # term (first insert, or takeover of an expired lease -- even by the same
    # holder after a release/lapse) and bumps ``epoch``, so the fencing token is
    # strictly increasing per acquisition as documented.
    _ACQUIRE_SQL = sqlalchemy.text(f"""
        INSERT INTO {_LEASE_TABLE} (lock_id, holder, epoch, expires_at)
        VALUES (:lock_id, :holder, 1, now() + make_interval(secs => :ttl))
        ON CONFLICT (lock_id) DO UPDATE
          SET holder = :holder,
              epoch = CASE WHEN {_LEASE_TABLE}.holder = :holder
                            AND {_LEASE_TABLE}.expires_at >= now()
                           THEN {_LEASE_TABLE}.epoch
                           ELSE {_LEASE_TABLE}.epoch + 1 END,
              expires_at = now() + make_interval(secs => :ttl)
          WHERE {_LEASE_TABLE}.holder = :holder
             OR {_LEASE_TABLE}.expires_at < now()
        RETURNING epoch
    """)

    # Renew is *not* the acquire upsert: it extends the expiry only while we
    # still hold a valid lease, and never changes ``holder`` or ``epoch``. This
    # gives renew a real precondition -- an elector that never won, or one whose
    # lease has lapsed or been taken over, gets zero rows and a ``False`` return
    # rather than silently (re)acquiring inside a renew. Re-acquisition after a
    # lapse goes through ``try_acquire`` instead, which bumps ``epoch``.
    _RENEW_SQL = sqlalchemy.text(f"""
        UPDATE {_LEASE_TABLE}
           SET expires_at = now() + make_interval(secs => :ttl)
         WHERE lock_id = :lock_id
           AND holder = :holder
           AND {_LEASE_TABLE}.expires_at >= now()
        RETURNING epoch
    """)

    # Immediate handoff: expire our own lease now so a follower can take over
    # without waiting out the TTL. Scoped to our holder so we never expire a
    # lease another replica has already taken from us.
    _RELEASE_SQL = sqlalchemy.text(f"""
        UPDATE {_LEASE_TABLE} SET expires_at = now()
        WHERE lock_id = :lock_id AND holder = :holder
    """)

    def __init__(
            self,
            lock_id: str,
            holder: Optional[str] = None,
            ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
            renew_interval_seconds: float = DEFAULT_RENEW_INTERVAL_SECONDS,
            renew_deadline_seconds: float = DEFAULT_RENEW_DEADLINE_SECONDS):
        super().__init__(lock_id)
        self._holder = holder or _HOLDER_ID
        self._ttl = ttl_seconds
        self._renew_interval = renew_interval_seconds
        self._renew_deadline = renew_deadline_seconds
        self._epoch: Optional[int] = None
        # Enforce the correctness ordering 0 < interval < deadline < ttl. The
        # strict ``deadline < ttl`` is the single-leader safety invariant (the
        # old leader stops at ``deadline`` before a follower can acquire at
        # ``ttl``); ``interval < deadline`` keeps a healthy renew inside the
        # window. A mis-tuned value fails loudly rather than silently narrowing
        # the margin. The ``statement_timeout < interval`` relationship (retry
        # budget vs. one hung renew) is a tuning concern, not a correctness one,
        # and is guarded on the shipped defaults at module load -- keeping it
        # out of here lets tests drive short (sub-``statement_timeout``) TTLs.
        if not (0 < renew_interval_seconds < renew_deadline_seconds <
                ttl_seconds):
            raise ValueError(
                'lease timing must satisfy 0 < interval < deadline < ttl, got '
                f'interval={renew_interval_seconds}, '
                f'deadline={renew_deadline_seconds}, ttl={ttl_seconds}')

    def _execute_bounded(self, sql, params, fetch):
        """Run *sql* in one short transaction bounded by ``statement_timeout``
        so a slow/locked DB fails fast rather than hanging the renew (→ step
        down) or the release (→ blocked re-contention) past the renew deadline.
        ``SET LOCAL`` scopes the timeout to this transaction only. Returns the
        fetched row when *fetch* is set, else None.

        This is an explicit READ COMMITTED transaction on purpose: ``SET LOCAL``
        only takes effect inside a transaction block, so do NOT "simplify" this
        to ``isolation_level='AUTOCOMMIT'`` -- under autocommit ``SET LOCAL``
        degrades to a WARNING no-op and the statement timeout silently
        disappears."""
        engine = db_utils.get_engine(None)
        with engine.connect() as conn:
            conn.execute(
                sqlalchemy.text(f'SET LOCAL statement_timeout = '
                                f'{_RENEW_STATEMENT_TIMEOUT_MS}'))
            result = conn.execute(sql, params)
            row = result.fetchone() if fetch else None
            conn.commit()
        return row

    def _run_epoch_stmt(self, sql) -> bool:
        """Run an ``epoch``-returning lease statement (acquire or renew).

        True iff a row came back (we hold the lease); records ``epoch`` as the
        fencing token. Any failure -- a DB blip, a statement-timeout, or a
        precondition miss (no row) -- clears the token and returns False, so the
        caller re-contends or steps down and the daemon never crashes on a
        transient error.
        """
        try:
            row = self._execute_bounded(sql, {
                'lock_id': self._lock_id,
                'holder': self._holder,
                'ttl': self._ttl,
            },
                                        fetch=True)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('%s: lease statement failed: %s', self._lock_id, e)
            self._epoch = None
            return False
        if row is None:
            self._epoch = None
            return False
        self._epoch = int(row[0])
        return True

    @property
    def renew_interval_seconds(self) -> float:
        return self._renew_interval

    @property
    def renew_deadline_seconds(self) -> Optional[float]:
        # Step down once we have not renewed for this long, so a follower does
        # not take the lease (grabbable only at ``ttl``) while we still believe
        # we hold it. ``deadline < ttl`` leaves the ``ttl - deadline`` margin.
        return self._renew_deadline

    def try_acquire(self) -> bool:
        return self._run_epoch_stmt(self._ACQUIRE_SQL)

    def renew(self) -> bool:
        return self._run_epoch_stmt(self._RENEW_SQL)

    def release(self) -> None:
        try:
            self._execute_bounded(self._RELEASE_SQL, {
                'lock_id': self._lock_id,
                'holder': self._holder,
            },
                                  fetch=False)
        except Exception as e:  # pylint: disable=broad-except
            # If we cannot expire the row, it will lapse on its own after the
            # TTL; a slower handoff, not a correctness problem.
            logger.debug('%s: lease release failed: %s', self._lock_id, e)
        finally:
            # Clear our fencing token only after the round-trip: until the
            # row is expired we may still be the named holder until the TTL.
            self._epoch = None

    def fencing_token(self) -> Optional[int]:
        return self._epoch


def get_backend() -> str:
    """Resolve the configured leader-election backend name."""
    value = os.environ.get(ENV_VAR_BACKEND, BACKEND_ADVISORY).strip().lower()
    return BACKEND_LEASE if value == BACKEND_LEASE else BACKEND_ADVISORY


def _postgres_available() -> bool:
    try:
        engine = global_user_state.initialize_and_get_db()
        return (
            engine.dialect.name == db_utils.SQLAlchemyDialect.POSTGRESQL.value)
    except Exception:  # pylint: disable=broad-except
        return False


def get_elector(
        lock_id: str,
        backend: Optional[str] = None,
        holder: Optional[str] = None,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS) -> LeaderElector:
    """Return a :class:`LeaderElector` for ``lock_id``.

    The lease backend is used only when it is both selected (via ``backend`` or
    ``SKYPILOT_LEADER_ELECTION_BACKEND``) and viable (Postgres configured);
    otherwise this falls back to the advisory backend, which itself degrades to
    a local file lock on single-node SQLite.
    """
    backend = backend or get_backend()
    if backend == BACKEND_LEASE and _postgres_available():
        return PgLeaseElector(lock_id, holder=holder, ttl_seconds=ttl_seconds)
    return AdvisoryLockElector(lock_id)

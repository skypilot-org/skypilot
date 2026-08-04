"""Leader election for singleton background work.

SkyPilot runs a number of fleet-wide singleton loops (one runner across all
API-server replicas). Historically the only way to elect that single runner was
a Postgres *session-scoped advisory lock* (``sky.utils.locks.PostgresLock``):
the winner holds one dedicated backend connection open for the entire duration
of its leadership, and loses the role only when that connection dies.

That has two structural costs:

1. **Connection cost.** The advisory lock is pinned to a direct (non-pooled)
   connection for as long as the role is held, so every held lock is one
   permanent backend connection that cannot ride a transaction-mode pooler.
2. **Fragile step-down.** Leadership loss is only observable by probing the
   connection (``is_session_alive``); a role reaped mid-work is not noticed
   until the next probe, and the lock carries no fencing token, so a stale
   leader's writes cannot be rejected.

This module abstracts leader election behind :class:`LeaderElector` and offers
a second, lease-based backend. A *lease* is a single row
(``leader_leases(lock_id, holder, epoch, expires_at)``) refreshed by a short
autocommit ``UPDATE``. Each renewal is a self-contained transaction, so it can
ride a pooled/pgBouncer connection and holds no persistent connection; the row
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

# Default lease time-to-live. A leader renews every ``ttl / 3`` and steps down
# if it has not renewed within ``2 * ttl / 3`` (the renew deadline), so a lost
# role is surfaced with a ``ttl / 3`` margin before the lease actually lapses --
# room for clock jitter, a stop-the-world pause, or transient DB latency.
DEFAULT_LEASE_TTL_SECONDS = 30.0

# Server-side bound on a single renew/acquire so a slow or locked DB cannot make
# the renew hang past the renew deadline undetected. Must stay well under
# ``renew_deadline_seconds`` (``2 * ttl / 3``). A timed-out renew raises and is
# treated as a lost role, which is the safe outcome.
_RENEW_STATEMENT_TIMEOUT_MS = 5000

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
        """Non-blocking bid for leadership. True iff this candidate now leads."""
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
        return DEFAULT_LEASE_TTL_SECONDS / 3

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

    Acquire and renew are the same atomic upsert: insert the lease if absent,
    else take it over only if we already hold it or the current lease has
    expired, bumping ``epoch`` on every change of holder. Every call is a short
    autocommit transaction on the pooled engine, so it rides a transaction-mode
    pooler and holds no persistent connection between renewals.
    """

    # Insert-or-take-over in one statement. All time math is server-side
    # (``now()``) so replicas never compare against their own wall clocks.
    # ``RETURNING`` yields a row iff we hold the lease after the statement: on
    # first insert, on renewal by the same holder, or on takeover of an expired
    # lease. When another holder's lease is still valid the ``DO UPDATE`` WHERE
    # is false, no row is written, and ``RETURNING`` is empty.
    _ACQUIRE_SQL = sqlalchemy.text(f"""
        INSERT INTO {_LEASE_TABLE} (lock_id, holder, epoch, expires_at)
        VALUES (:lock_id, :holder, 1, now() + make_interval(secs => :ttl))
        ON CONFLICT (lock_id) DO UPDATE
          SET holder = :holder,
              epoch = CASE WHEN {_LEASE_TABLE}.holder = :holder
                           THEN {_LEASE_TABLE}.epoch
                           ELSE {_LEASE_TABLE}.epoch + 1 END,
              expires_at = now() + make_interval(secs => :ttl)
          WHERE {_LEASE_TABLE}.holder = :holder
             OR {_LEASE_TABLE}.expires_at < now()
        RETURNING epoch
    """)

    # Immediate handoff: expire our own lease now so a follower can take over
    # without waiting out the TTL. Scoped to our holder so we never expire a
    # lease another replica has already taken from us.
    _RELEASE_SQL = sqlalchemy.text(f"""
        UPDATE {_LEASE_TABLE} SET expires_at = now()
        WHERE lock_id = :lock_id AND holder = :holder
    """)

    def __init__(self,
                 lock_id: str,
                 holder: Optional[str] = None,
                 ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS):
        super().__init__(lock_id)
        self._holder = holder or _HOLDER_ID
        self._ttl = ttl_seconds
        self._epoch: Optional[int] = None

    def _acquire_or_renew(self) -> bool:
        engine = db_utils.get_engine(None)
        try:
            with engine.connect() as conn:
                # Bound the renew server-side: a slow/locked DB must fail the
                # renew (→ step down) rather than hang past the renew deadline.
                # SET LOCAL scopes it to this transaction only.
                conn.execute(
                    sqlalchemy.text(f'SET LOCAL statement_timeout = '
                                    f'{_RENEW_STATEMENT_TIMEOUT_MS}'))
                row = conn.execute(
                    self._ACQUIRE_SQL, {
                        'lock_id': self._lock_id,
                        'holder': self._holder,
                        'ttl': self._ttl,
                    }).fetchone()
                conn.commit()
        except Exception as e:  # pylint: disable=broad-except
            # A DB blip (or a statement-timeout) is treated as a lost/failed
            # term: the caller re-contends or steps down. Never crash the daemon
            # on a transient error.
            logger.warning('%s: lease upsert failed: %s', self._lock_id, e)
            self._epoch = None
            return False
        if row is None:
            self._epoch = None
            return False
        self._epoch = int(row[0])
        return True

    @property
    def renew_interval_seconds(self) -> float:
        # Renew at a third of the TTL: two consecutive missed renewals still
        # leave a ``ttl / 3`` margin before the lease lapses.
        return self._ttl / 3

    @property
    def renew_deadline_seconds(self) -> Optional[float]:
        # Step down once we are within one renew interval of expiry, so another
        # replica does not take the lease while we still believe we hold it.
        return self._ttl * 2 / 3

    def try_acquire(self) -> bool:
        return self._acquire_or_renew()

    def renew(self) -> bool:
        return self._acquire_or_renew()

    def release(self) -> None:
        self._epoch = None
        engine = db_utils.get_engine(None)
        try:
            with engine.connect() as conn:
                conn.execute(self._RELEASE_SQL, {
                    'lock_id': self._lock_id,
                    'holder': self._holder,
                })
                conn.commit()
        except Exception as e:  # pylint: disable=broad-except
            # If we cannot expire the row, it will lapse on its own after the
            # TTL; a slower handoff, not a correctness problem.
            logger.debug('%s: lease release failed: %s', self._lock_id, e)

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

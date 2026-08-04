"""Unit tests for sky.utils.leader_election.

These cover the backend-selection logic and the advisory backend on the local
file-lock path (no Postgres required). Where a Postgres fixture is available
they also cover the lease backend's SQL, and drive both backends through a
property-based state machine of several contenders for one lock.
"""
import os
import re
import threading
import time
from typing import Dict, Optional, Tuple
from unittest import mock

import hypothesis
from hypothesis import stateful
from hypothesis import strategies as st
import pytest
import sqlalchemy

from sky import global_user_state
from sky.utils import leader_election
from sky.utils import locks


@pytest.mark.parametrize('env_value,expected', [
    (None, leader_election.BACKEND_ADVISORY),
    ('', leader_election.BACKEND_ADVISORY),
    ('advisory', leader_election.BACKEND_ADVISORY),
    ('lease', leader_election.BACKEND_LEASE),
    ('LEASE', leader_election.BACKEND_LEASE),
    ('  lease  ', leader_election.BACKEND_LEASE),
    ('bogus', leader_election.BACKEND_ADVISORY),
])
def test_get_backend(env_value, expected, monkeypatch):
    if env_value is None:
        monkeypatch.delenv(leader_election.ENV_VAR_BACKEND, raising=False)
    else:
        monkeypatch.setenv(leader_election.ENV_VAR_BACKEND, env_value)
    assert leader_election.get_backend() == expected


def test_holder_id_is_shaped_hostname_pid_random_suffix():
    # Shaped ``<host>-<pid>-<8 hex>``. Both pid and the random suffix matter:
    # the suffix guards pid reuse against a not-yet-expired lease row, so assert
    # its shape explicitly (a plain dash count is satisfied by a hostname with
    # dashes and would not catch the suffix being dropped).
    holder = leader_election._HOLDER_ID
    assert f'-{os.getpid()}-' in holder
    assert re.search(r'-[0-9a-f]{8}$', holder), holder


def test_get_elector_defaults_to_advisory(monkeypatch):
    monkeypatch.delenv(leader_election.ENV_VAR_BACKEND, raising=False)
    elector = leader_election.get_elector('some-lock')
    assert isinstance(elector, leader_election.AdvisoryLockElector)


def test_get_elector_lease_requires_postgres(monkeypatch):
    monkeypatch.setenv(leader_election.ENV_VAR_BACKEND,
                       leader_election.BACKEND_LEASE)
    # Lease selected but Postgres unavailable -> fall back to advisory.
    with mock.patch.object(leader_election,
                           '_postgres_available',
                           return_value=False):
        assert isinstance(leader_election.get_elector('lock'),
                          leader_election.AdvisoryLockElector)
    # Lease selected and Postgres available -> lease backend.
    with mock.patch.object(leader_election,
                           '_postgres_available',
                           return_value=True):
        assert isinstance(leader_election.get_elector('lock'),
                          leader_election.PgLeaseElector)


def test_lease_timing_is_independently_tunable():
    """Interval and deadline are independent knobs (not derived from the TTL),
    and the constructor enforces 0 < interval < deadline < ttl."""
    e = leader_election.PgLeaseElector('lock',
                                       holder='a',
                                       ttl_seconds=6,
                                       renew_interval_seconds=1,
                                       renew_deadline_seconds=3)
    assert e.renew_interval_seconds == 1
    assert e.renew_deadline_seconds == 3
    # Defaults are the conservative 60/10/30.
    d = leader_election.PgLeaseElector('lock', holder='a')
    assert d.renew_interval_seconds == 10
    assert d.renew_deadline_seconds == 30
    # deadline >= ttl (or any out-of-order timing) is rejected.
    with pytest.raises(ValueError):
        leader_election.PgLeaseElector('lock',
                                       ttl_seconds=6,
                                       renew_interval_seconds=4,
                                       renew_deadline_seconds=6)
    # Advisory has no time-bounded lease -> no renew deadline.
    assert leader_election.AdvisoryLockElector('lock').renew_deadline_seconds \
        is None


def test_advisory_elector_filelock_lifecycle(monkeypatch):
    """On the (non-Postgres) file-lock path, renew is always true while held."""
    monkeypatch.delenv(leader_election.ENV_VAR_BACKEND, raising=False)
    lock_id = 'test-leader-election-lifecycle'
    a = leader_election.AdvisoryLockElector(lock_id)
    b = leader_election.AdvisoryLockElector(lock_id)

    assert a.try_acquire() is True
    # A file lock is held for the life of the process; renew never lapses it.
    assert a.renew() is True
    assert a.fencing_token() is None
    # Second candidate cannot take the same lock while A holds it.
    assert b.try_acquire() is False

    a.release()
    # After release, renew reports no leadership and B can take over.
    assert a.renew() is False
    assert b.try_acquire() is True
    b.release()


# --- Postgres-backed lease SQL (opt-in via SKYPILOT_TEST_PG_URL) -----------
#
# The lease backend's acquire/renew/expire/takeover SQL only runs against
# Postgres, so it has no coverage in the mock-based tests above. These exercise
# it against a real server, gated on an external ``SKYPILOT_TEST_PG_URL`` (a
# throwaway local server or a CI service) and skipped otherwise -- an escape
# hatch so a change to ``_ACQUIRE_SQL`` / ``_RENEW_SQL`` is runnable in-tree.

_PG_URL = os.environ.get('SKYPILOT_TEST_PG_URL')
pg_only = pytest.mark.skipif(not _PG_URL,
                             reason='SKYPILOT_TEST_PG_URL is not set')


def _expire_now(engine, lock_id):
    """Force a lease to look expired without waiting out its TTL."""
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text('UPDATE leader_leases SET expires_at = now() '
                            '- make_interval(secs => 1) WHERE lock_id = :l'),
            {'l': lock_id})
        conn.commit()


@pytest.fixture()
def lease_db(monkeypatch):
    """A clean ``leader_leases`` table, elector pointed at the test DB."""
    # NullPool, matching the direct engine the server hands to a session-scoped
    # advisory lock (``db_utils.get_engine(..., direct=True)``): every checkout
    # is its own connection, closed on release. It also keeps reaping a session
    # from throwing off a reuse pool's overflow accounting, which otherwise
    # drifts until checkout blocks even though the server has a handful of
    # connections open.
    engine = sqlalchemy.create_engine(_PG_URL, poolclass=sqlalchemy.NullPool)
    global_user_state.leader_leases_table.create(engine, checkfirst=True)
    monkeypatch.setattr(leader_election.db_utils, 'get_engine',
                        lambda *a, **k: engine)
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text('DELETE FROM leader_leases'))
        conn.commit()
    yield engine
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text('DELETE FROM leader_leases'))
        conn.commit()
    engine.dispose()


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_first_acquire_wins_and_second_holder_is_locked_out(lease_db):
    a = leader_election.PgLeaseElector('lock', holder='a')
    b = leader_election.PgLeaseElector('lock', holder='b')

    assert a.try_acquire() is True
    assert a.fencing_token() == 1
    # b cannot take a valid lease held by a.
    assert b.try_acquire() is False
    assert b.fencing_token() is None


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_renew_keeps_the_same_epoch(lease_db):
    a = leader_election.PgLeaseElector('lock', holder='a')
    assert a.try_acquire() is True
    assert a.renew() is True
    assert a.renew() is True
    # Renewing a still-valid lease never bumps the fencing token.
    assert a.fencing_token() == 1


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_renew_requires_a_valid_held_lease(lease_db):
    a = leader_election.PgLeaseElector('lock', holder='a')
    # An elector that never won cannot gain leadership via renew().
    assert a.renew() is False
    assert a.fencing_token() is None
    # And a lapsed lease fails renew() instead of silently re-acquiring it.
    assert a.try_acquire() is True
    _expire_now(lease_db, 'lock')
    assert a.renew() is False


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_takeover_after_expiry_bumps_the_epoch(lease_db):
    a = leader_election.PgLeaseElector('lock', holder='a')
    b = leader_election.PgLeaseElector('lock', holder='b')

    assert a.try_acquire() is True
    _expire_now(lease_db, 'lock')

    # b takes over the expired lease; the epoch increments so a stale write
    # from a can be fenced out.
    assert b.try_acquire() is True
    assert b.fencing_token() == 2
    # a's renew now fails -- b holds a fresh, unexpired lease.
    assert a.renew() is False


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_release_then_reacquire_by_same_holder_bumps_epoch(lease_db):
    # A clean step-down and re-contention is a *new* term, so the fencing token
    # must advance even though the holder is unchanged.
    a = leader_election.PgLeaseElector('lock', holder='a')
    assert a.try_acquire() is True
    assert a.fencing_token() == 1
    a.release()
    assert a.try_acquire() is True
    assert a.fencing_token() == 2


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_release_hands_over_immediately(lease_db):
    a = leader_election.PgLeaseElector('lock', holder='a')
    b = leader_election.PgLeaseElector('lock', holder='b')

    assert a.try_acquire() is True
    # b is locked out while a holds a valid lease...
    assert b.try_acquire() is False
    # ...until a releases, which expires the lease immediately.
    a.release()
    assert a.fencing_token() is None
    assert b.try_acquire() is True
    assert b.fencing_token() == 2


# --- Multi-actor election, property-based ----------------------------------
#
# The tests above each walk one hand-written transition. This drives several
# contenders for the same ``lock_id`` through arbitrary interleavings of
# acquire / renew / release plus a fault that takes the role away from the
# holder without the holder noticing, and checks the election's invariants
# after every step.
#
# Electors are plain synchronous objects, so the machine makes one call at a
# time against a real Postgres -- no waiting out a TTL, and a failing run
# replays exactly as printed. Partly-completed work is expressed by stopping a
# real elector call at its own pre-commit and leaving it there while the other
# actors keep going (see :class:`_ParkedCall`); the park point doubles as the
# synchronisation point, so the machine is still one call at a time.
#
# Both backends implement the same ``LeaderElector`` contract, so one machine
# drives both. They differ in three places, and only those live in the adapters
# below; every invariant is shared.

# A lease TTL far longer than a run, so a lease only ever ends because the
# machine ended it: expiry is a rule, never a race with the wall clock.
_MACHINE_TTL_SECONDS = 300.0

# Enough contenders for a takeover chain: hand the role from one to a second
# while a third contends for it.
_MACHINE_ACTORS = 3

# Shortens -- not disables -- the module's shipped
# ``_RENEW_STATEMENT_TIMEOUT_MS`` (5s), which is what turns a statement blocked
# behind a parked call into a bounded failure. Every step that contends with a
# parked lease row waits this out, so the shipped bound would leave the machine
# unusably slow. It caps server-side execution, which for these statements is
# well under a millisecond, so the margin here is large.
_PARKED_STATEMENT_TIMEOUT_MS = 250

# How long to wait on any of the parking machinery's synchronisation points:
# a call reaching its commit, a call finishing once let go, and the hook's own
# release wait. All are bounded so a mistake in the parking machinery fails
# the test instead of stalling the run; none is a timing assertion, since the
# work being waited on is a single local statement.
_PARK_WAIT_SECONDS = 10.0

# The rules talk about statements; the elector exposes methods. Naming both
# here means renaming a method breaks the park loudly rather than silently
# parking nothing.
_PARKED_METHODS = {
    'acquire': 'try_acquire',
    'renew': 'renew',
    'release': 'release',
}

# Distinguishes "not read yet" from a real reading of None (nobody leads).
_UNREAD = object()

_MACHINE_SETTINGS = hypothesis.settings(
    max_examples=100,
    stateful_step_count=20,
    # Every step is a database round trip, so per-example timing says nothing
    # about the code under test.
    deadline=None,
    suppress_health_check=[
        hypothesis.HealthCheck.too_slow,
        # The database fixture is deliberately not rebuilt per example: the
        # machine clears the election itself in ``__init__`` (via
        # ``backend.reset``), so every example still starts clean.
        hypothesis.HealthCheck.function_scoped_fixture,
    ],
)


class _ParkSlot:
    """The handshake between one helper thread and the commit hook."""

    def __init__(self):
        self.parked = threading.Event()
        self.release = threading.Event()
        self.crash = False
        self.returned = None
        self.error = None


class _InFlightCall:
    """A real elector call running on a thread of its own.

    Held at its own pre-commit when :class:`_CommitParker` was told to hold it;
    otherwise it runs straight through. Either way this is the handle the test
    uses to see where the call has got to and to collect what it returned.
    """

    def __init__(self, slot, thread, label):
        self._slot = slot
        self._thread = thread
        self.label = label

    def wait_until_parked(self, timeout: float = _PARK_WAIT_SECONDS) -> bool:
        """True once the call is sitting in its pre-commit."""
        return self._slot.parked.wait(timeout)

    def reached_its_commit(self) -> bool:
        return self._slot.parked.is_set()

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def finish(self, *, crash: bool = False):
        """Let the call complete and hand back what it returned.

        With *crash*, the hook raises instead of returning, so the commit never
        reaches the server and the exception travels the module's own failure
        path out of ``_execute_bounded``.
        """
        self._slot.crash = crash
        self._slot.release.set()
        self._thread.join(_PARK_WAIT_SECONDS)
        assert not self._thread.is_alive(), (
            f'{self.label} did not finish after being released')
        assert self._slot.error is None, (
            f'{self.label} raised out of the elector: {self._slot.error!r}')
        return self._slot.returned


class _ParkedCall(_InFlightCall):
    """A call the machine is holding, plus what it predicts the call did.

    Its statement has run and holds whatever lock it took, but nothing it wrote
    is visible to any other connection, and it has not yet returned, so its
    result and fencing token do not exist yet. That is why nothing is asserted
    about the statement at park time: what it produced is checked when the call
    is let go.

    Letting it go is the only thing that ends it. A contender that blocks
    behind it does not: that contender dies at the statement timeout and
    reports a lost role, which is an outcome to assert on.
    """

    def __init__(self, slot, thread, actor, statement, wrote, pending_epoch,
                 row_existed):
        super().__init__(slot, thread, f'parked {statement} by {actor}')
        self.actor = actor
        self.statement = statement
        # What the model says the statement will have produced, checked against
        # the call's own return value once it is let go.
        self.wrote = wrote
        self.pending_epoch = pending_epoch
        if statement == 'acquire':
            # An upsert locks the row it conflicts with even when its DO UPDATE
            # WHERE turns out false, so a refused acquire blocks the next
            # writer exactly as a winning one does. With no row to conflict
            # with it is a speculative insert instead, which only another
            # inserter waits on: an UPDATE cannot see the row, so it never
            # reaches for the lock.
            self.blocks_acquire = True
            self.blocks_row_write = row_existed
        else:
            # A renew that matched holds the row; one that matched nothing
            # took no lock and blocks nobody.
            self.blocks_acquire = wrote
            self.blocks_row_write = wrote


class _CommitParker:
    """Stops a lease statement at its own pre-commit, via the engine's events.

    ``ConnectionEvents.commit`` runs before the COMMIT reaches the server: rows
    the transaction wrote are still invisible to every other connection, the
    locks it took are still held, and raising from the listener aborts the
    commit and propagates to the caller. That makes it a fault-injection point
    for "the process stopped between the write and the commit" without touching
    the code under test -- the same idea as etcd's
    ``// gofail: var beforeCommit struct{}`` in
    server/storage/backend/batch_tx.go.

    The listener sees every commit on the engine, including the test's own
    resets and the elector calls other rules make on the main thread, so it
    parks only a thread that armed itself: the registry is keyed by
    ``threading.get_ident()`` and each entry parks once. Anything else returns
    immediately and commits normally.
    """

    def __init__(self, engine):
        self._slots: Dict[int, _ParkSlot] = {}
        sqlalchemy.event.listen(engine, 'commit', self._on_commit)

    def _on_commit(self, conn) -> None:
        # Which connection is committing does not matter; that this thread is
        # about to commit does.
        del conn
        slot = self._slots.pop(threading.get_ident(), None)
        if slot is None:
            return
        slot.parked.set()
        if not slot.release.wait(_PARK_WAIT_SECONDS):
            # Raising aborts this commit, which beats stalling the run: the
            # call reports a lost role and the machine fails on the mismatch.
            raise AssertionError('parked call was never released')
        if slot.crash:
            raise RuntimeError('injected failure before commit')

    def _launch(self, elector, statement: str, name: str, hold: bool):
        """Run the elector call for *statement* on an armed thread."""
        method = getattr(elector, _PARKED_METHODS[statement])
        slot = _ParkSlot()
        if not hold:
            # Armed only so the caller can see the call reach its commit; the
            # hook records that and lets it straight through.
            slot.release.set()

        def run() -> None:
            # Armed from inside the thread, before the call starts, so the
            # registry is populated by the time the hook could fire.
            self._slots[threading.get_ident()] = slot
            try:
                slot.returned = method()
            except BaseException as e:  # pylint: disable=broad-except
                slot.error = e

        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        return slot, thread

    def start(self, elector, statement: str, label: str, *,
              hold: bool) -> _InFlightCall:
        slot, thread = self._launch(elector, statement, label, hold)
        return _InFlightCall(slot, thread, label)

    def park(self, elector, statement: str, actor: str, *, wrote: bool,
             pending_epoch: int, row_existed: bool) -> _ParkedCall:
        """Start the elector call for *statement* on a self-parking thread."""
        slot, thread = self._launch(elector, statement,
                                    f'parked-{statement}-{actor}', True)
        return _ParkedCall(slot, thread, actor, statement, wrote, pending_epoch,
                           row_existed)


class _LeaseBackend:
    """The machine's lease-backend world.

    Builds real :class:`leader_election.PgLeaseElector` instances and supplies
    what the machine needs around them: the fault (force-expire the row), the
    server oracle (the ``leader_leases`` row), per-example reset, and the
    pre-commit park seam.
    """

    has_fencing_token = True
    # Losing the role leaves the row in place, named but expired. That is what
    # lets a same-holder re-acquire be told apart from a first acquire.
    loss_keeps_holder = True
    can_park = True

    def __init__(self, engine, lock_id):
        self._engine = engine
        self._lock_id = lock_id
        self._parker = _CommitParker(engine)

    def reset(self) -> None:
        with self._engine.connect() as conn:
            # Bounded so that a parked call some earlier teardown failed to
            # end shows up here as a cancelled statement. Unbounded, this
            # DELETE would wait on its row lock for as long as the run lasts.
            conn.execute(
                sqlalchemy.text(
                    'SET LOCAL statement_timeout = '
                    f'{leader_election._RENEW_STATEMENT_TIMEOUT_MS}'))
            conn.execute(
                sqlalchemy.text('DELETE FROM leader_leases WHERE '
                                'lock_id = :l'), {'l': self._lock_id})
            conn.commit()

    def new_elector(self, actor: str) -> leader_election.LeaderElector:
        return leader_election.PgLeaseElector(self._lock_id,
                                              holder=actor,
                                              ttl_seconds=_MACHINE_TTL_SECONDS)

    def note_acquired(self, actor: str,
                      elector: leader_election.LeaderElector) -> None:
        del actor, elector  # The row names its own holder.

    def observe(self) -> Optional[Tuple[str, Optional[int], bool]]:
        """``(holder, epoch, expired)`` as the server has it, or None."""
        # ``lock_id`` is the primary key, so one row is all the server can
        # hold: mutual exclusion here is structural, not something to check.
        with self._engine.connect() as conn:
            row = conn.execute(
                sqlalchemy.text('SELECT holder, epoch, expires_at < now() '
                                'FROM leader_leases WHERE lock_id = :l'), {
                                    'l': self._lock_id
                                }).fetchone()
        if row is None:
            return None
        return (row[0], int(row[1]), bool(row[2]))

    def fault(self) -> None:
        _expire_now(self._engine, self._lock_id)

    def start_parked(self, elector, statement: str, actor: str, *, wrote: bool,
                     pending_epoch: int, row_existed: bool) -> _ParkedCall:
        """Stop a real call by *elector* at its own pre-commit."""
        return self._parker.park(elector,
                                 statement,
                                 actor,
                                 wrote=wrote,
                                 pending_epoch=pending_epoch,
                                 row_existed=row_existed)

    def start_in_flight(self, elector, statement: str, label: str, *,
                        hold: bool) -> _InFlightCall:
        """Run one elector call on its own thread, held pre-commit or not."""
        return self._parker.start(elector, statement, label, hold=hold)


class _AdvisoryBackend:
    """The machine's advisory-backend world, on Postgres.

    Builds real :class:`leader_election.AdvisoryLockElector` instances and
    supplies the fault (reap the holder's session), the server oracle
    (``pg_locks``, named through the pids recorded at acquire), and per-example
    reset.
    """

    has_fencing_token = False
    # A reaped or released session frees the lock outright; the server keeps
    # no record of who held it.
    loss_keeps_holder = False
    # An advisory lock is already held across statements by its own session, so
    # there is no uncommitted write to leave in flight.
    can_park = False

    def __init__(self, engine, lock_id):
        self._engine = engine
        self._lock_id = lock_id
        # Same key derivation the lock itself uses, so ``pg_locks`` can be
        # searched for exactly the lock the electors are contending for.
        self._lock_key = locks.PostgresLock(lock_id)._lock_key
        self._actor_by_pid = {}

    def reset(self) -> None:
        self._actor_by_pid = {}
        assert self._holder_pids() == [], (
            'a previous example left the advisory lock held')

    def new_elector(self, actor: str) -> leader_election.LeaderElector:
        del actor  # Identity is the Postgres session, not a name.
        return leader_election.AdvisoryLockElector(self._lock_id)

    def note_acquired(self, actor: str,
                      elector: leader_election.LeaderElector) -> None:
        """Record which session now holds the lock, so it can be named."""
        # Deliberate reach into the lock's connection: the backend pid is the
        # only handle on "which contender does the server think holds this",
        # which is what the lease backend gets for free from ``holder``.
        connection = elector._lock._connection
        cursor = connection.cursor()
        try:
            cursor.execute('SELECT pg_backend_pid()')
            self._actor_by_pid[cursor.fetchone()[0]] = actor
        finally:
            cursor.close()

    def _holder_pids(self):
        with self._engine.connect() as conn:
            rows = conn.execute(
                sqlalchemy.text(
                    'SELECT pid FROM pg_locks WHERE locktype = \'advisory\' '
                    'AND granted '
                    'AND ((classid::bigint << 32) | objid::bigint) = :key'), {
                        'key': self._lock_key
                    }).fetchall()
        return [row[0] for row in rows]

    def observe(self) -> Optional[Tuple[str, Optional[int], bool]]:
        """``(holder, None, False)`` for the holding session, or None."""
        pids = self._holder_pids()
        # Mutual exclusion as the server sees it: an exclusive advisory lock
        # is held by at most one session, whatever the electors believe.
        assert len(pids) <= 1, f'{len(pids)} sessions hold the lock: {pids}'
        if not pids:
            return None
        actor = self._actor_by_pid.get(pids[0])
        assert actor is not None, f'session {pids[0]} holds an unclaimed lock'
        return (actor, None, False)

    def fault(self) -> None:
        """Reap the holder's session, the way a database restart would."""
        pids = self._holder_pids()
        assert len(pids) == 1, f'nothing to reap: {pids}'
        with self._engine.connect() as conn:
            conn.execute(sqlalchemy.text('SELECT pg_terminate_backend(:p)'),
                         {'p': pids[0]})
            conn.commit()


class LeaderElectionMachine(stateful.RuleBasedStateMachine):
    """Contenders for one ``lock_id``, one elector call per step.

    The model tracks what the server should hold and what each actor last
    learned about itself. Every rule asserts the call's return value against
    the model *before* applying the transition, so a wrong answer fails at the
    step that produced it; the invariants then re-check the model against what
    the server actually says.
    """

    actors = stateful.Bundle('actors')
    parked = stateful.Bundle('parked')

    def __init__(self, backend):
        super().__init__()
        self._backend = backend
        self._backend.reset()
        self._electors = {}
        # What each actor last learned about itself: True after a successful
        # try_acquire/renew, False after any failure or after release.
        self._believes_leader = {}
        # An actor whose term a fault ended, which the actor has not yet
        # observed. It is obliged to have stepped down already -- the lease is
        # past its renew deadline, or the advisory session is gone -- so it
        # does not count as a leader, and the point of the fencing token is
        # that its writes can still be told from the new leader's.
        self._must_step_down = {}
        # Who the server names as holder, whether or not that name still
        # leads (see ``loss_keeps_holder``), and the term's fencing token.
        self._holder = None
        self._holder_expired = False
        self._epoch = 0
        self._max_epoch = 0
        # Whether the actor the server names as holder still believes it.
        # False only where the model knows otherwise: a call cancelled at the
        # statement timeout because it contended with a parked one, and a
        # parked call that failed before its commit. Both step an actor down
        # while the row goes on naming it, which the lease's TTL cleans up.
        self._holder_knows_it_leads = True
        self._joined = 0
        self._server_state = _UNREAD
        self._parked = None

    def _server(self) -> Optional[Tuple[str, Optional[int], bool]]:
        """One reading of the server per step, shared by the invariants.

        Only a rule moves the election, and every rule that can move it
        marks this stale on the way in, so the invariants can all read the same
        snapshot instead of each paying for its own round trip.
        """
        if self._server_state is _UNREAD:
            self._server_state = self._backend.observe()
        return self._server_state

    def _leader(self) -> Optional[str]:
        """The actor the server would let keep the role right now."""
        if self._holder is None or self._holder_expired:
            return None
        return self._holder

    def _lose_role(self) -> None:
        if self._backend.loss_keeps_holder:
            self._holder_expired = True
        else:
            self._holder = None
        self._holder_knows_it_leads = True

    def _blocks_acquire(self) -> bool:
        return self._parked is not None and self._parked.blocks_acquire

    def _blocks_row_write(self) -> bool:
        return self._parked is not None and self._parked.blocks_row_write

    def _has_a_call_in_flight(self, actor: str) -> bool:
        """Whether this actor's own elector is already inside a call.

        An elector belongs to one runner, which cannot start a second call
        until the first returns. Overlapping two calls on one object would only
        race them over its fencing token -- last writer wins, and the earlier
        call's answer is silently lost -- which is not something the module
        offers to survive, so the machine does not ask it to.
        """
        return self._parked is not None and self._parked.actor == actor

    def _blocked_out(self, actor: str) -> None:
        """Note that the sitting leader has just been told it lost the role.

        Either a statement of its own was cancelled behind a parked call, or a
        parked call of its own failed before committing. Either way the actor
        steps down while the row goes on naming it and no one else can take it
        -- a deferred handover, resolved when the lease expires.
        """
        if self._leader() == actor:
            self._holder_knows_it_leads = False

    def _state(self) -> str:
        return (f'holder={self._holder} expired={self._holder_expired} '
                f'epoch={self._epoch} believes={self._believes_leader} '
                f'must_step_down={self._must_step_down}')

    def _join(self) -> str:
        actor = f'actor-{self._joined}'
        self._joined += 1
        self._electors[actor] = self._backend.new_elector(actor)
        self._believes_leader[actor] = False
        self._must_step_down[actor] = False
        return actor

    # --- rules ---

    @stateful.initialize(target=actors)
    def first_contenders(self):
        """Start with two contenders, so contention is reachable from step one.

        With an empty bundle only ``join`` can run and the generator spends its
        budget drawing rules it has to discard; with a single contender it can
        never draw a bid against a lock someone else holds.
        """
        return stateful.multiple(self._join(), self._join())

    @stateful.precondition(lambda self: len(self._electors) < _MACHINE_ACTORS)
    @stateful.rule(target=actors)
    def join(self):
        """A fresh contender starts up and joins the election."""
        self._server_state = _UNREAD
        return self._join()

    @stateful.rule(actor=actors)
    def acquire(self, actor):
        if self._has_a_call_in_flight(actor):
            hypothesis.event('acquire: actor already has a call in flight')
            return
        self._server_state = _UNREAD
        elector = self._electors[actor]
        leader = self._leader()
        if self._blocks_acquire():
            # The upsert waits on the parked transaction's row and dies at the
            # statement timeout. A lost role is the safe reading of a bid that
            # could not be resolved, so this is False even where an unobstructed
            # bid would have won.
            expected = False
            hypothesis.event('acquire: blocked by a parked transaction')
        elif leader is None:
            # LIVENESS: nobody holds the role, so this bid has to win. A
            # backend that could refuse here would leave the lock dead after
            # a handover or a fault.
            expected = True
            hypothesis.event('acquire: free')
        elif leader == actor:
            expected = True
            hypothesis.event('acquire: held by self')
        else:
            expected = False
            hypothesis.event('acquire: held by another')

        result = elector.try_acquire()
        assert result is expected, (f'try_acquire by {actor}: got {result}, '
                                    f'expected {expected}; {self._state()}')
        if not result:
            self._believes_leader[actor] = False
            if self._blocks_acquire():
                self._blocked_out(actor)
            return

        if self._backend.has_fencing_token:
            # TERM DISCRIMINATION: keeping the token is only correct for the
            # holder re-bidding on a lease it still validly holds; every other
            # acquire starts a term a stale leader must be fenced out of.
            same_term = (self._holder == actor and not self._holder_expired)
            if not same_term:
                self._epoch += 1
            assert elector.fencing_token() == self._epoch, (
                f'acquire by {actor} handed out token '
                f'{elector.fencing_token()}, expected {self._epoch}')
        self._holder = actor
        self._holder_expired = False
        self._holder_knows_it_leads = True
        self._believes_leader[actor] = True
        self._must_step_down[actor] = False
        self._backend.note_acquired(actor, elector)

    @stateful.rule(actor=actors)
    def renew(self, actor):
        if self._has_a_call_in_flight(actor):
            hypothesis.event('renew: actor already has a call in flight')
            return
        self._server_state = _UNREAD
        elector = self._electors[actor]
        if self._blocks_row_write():
            # The extend UPDATE waits on the parked row and dies at the
            # statement timeout, so even the sitting leader is told it has
            # lost the role.
            expected = False
            hypothesis.event('renew: blocked by a parked transaction')
        else:
            expected = self._leader() == actor
            hypothesis.event(f'renew -> {expected}')

        result = elector.renew()
        assert result is expected, (f'renew by {actor}: got {result}, '
                                    f'expected {expected}; {self._state()}')
        if result:
            self._holder_knows_it_leads = True
            if self._backend.has_fencing_token:
                # A renew confirms the running term; it never starts a new one.
                assert elector.fencing_token() == self._epoch, (
                    f'renew by {actor} moved the token to '
                    f'{elector.fencing_token()}, expected {self._epoch}')
        else:
            if self._blocks_row_write():
                self._blocked_out(actor)
        self._believes_leader[actor] = result
        self._must_step_down[actor] = False

    @stateful.rule(actor=actors)
    def release(self, actor):
        if self._has_a_call_in_flight(actor):
            hypothesis.event('release: actor already has a call in flight')
            return
        self._server_state = _UNREAD
        elector = self._electors[actor]
        was_leader = self._leader() == actor
        hypothesis.event(f'release: leading={was_leader}')

        blocked = self._blocks_row_write()
        # Documented as safe whether or not this actor leads, so an exception
        # here is itself the failure.
        elector.release()
        if self._backend.has_fencing_token:
            assert elector.fencing_token() is None, (
                f'{actor} kept token {elector.fencing_token()} after release')
        if self._holder == actor:
            if blocked:
                # The expiry UPDATE waits on the parked row, dies at the
                # statement timeout, and release swallows that by design: the
                # actor has stepped down but the row keeps naming it until the
                # TTL runs out. Handover is slower, not unsafe.
                hypothesis.event('release: expiry blocked, handover deferred')
                self._blocked_out(actor)
            else:
                self._lose_role()
        self._believes_leader[actor] = False
        self._must_step_down[actor] = False

    # A blocked expiry would wait on a row lock that only this thread can
    # lift, since it is the one that frees a parked call, and the test's expiry
    # helper is unbounded -- so it would wait out the rest of the run.
    @stateful.precondition(
        lambda self: self._leader() is not None and self._parked is None)
    @stateful.rule()
    def fault(self):
        """Take the role away without telling the holder."""
        self._server_state = _UNREAD
        holder = self._leader()
        hypothesis.event('fault')
        self._backend.fault()
        self._lose_role()
        self._must_step_down[holder] = True

    # At most one at a time: every actor contends for the same lease row, and
    # both parkable statements write it, so a second call would block on the
    # first and die at the statement timeout before ever reaching its commit.
    # See the second-writer test below for the assertion that pins this.
    @stateful.precondition(
        lambda self: self._backend.can_park and self._parked is None)
    @stateful.rule(target=parked,
                   actor=actors,
                   statement=st.sampled_from(('acquire', 'renew')))
    def park_transaction(self, actor, statement):
        """Stop a real elector call at its own pre-commit and leave it there.

        Nothing is asserted about the statement here: it has run, but the call
        has not returned, so its result and fencing token do not exist yet.
        Both are checked when the call is let go.
        """
        self._server_state = _UNREAD
        row_existed = self._holder is not None
        if statement == 'acquire':
            wrote = (not row_existed or self._holder == actor or
                     self._holder_expired)
            same_term = (self._holder == actor and not self._holder_expired)
            pending_epoch = self._epoch if same_term else self._epoch + 1
        else:
            wrote = self._leader() == actor
            pending_epoch = self._epoch
        hypothesis.event(f'park {statement}: wrote={wrote}')

        # Recorded before the wait so a park that never arrives is still torn
        # down rather than left running.
        self._parked = self._backend.start_parked(self._electors[actor],
                                                  statement,
                                                  actor,
                                                  wrote=wrote,
                                                  pending_epoch=pending_epoch,
                                                  row_existed=row_existed)
        assert self._parked.wait_until_parked(), (
            f'{statement} by {actor} never reached its commit; '
            f'{self._state()}')
        return self._parked

    @stateful.rule(handle=stateful.consumes(parked))
    def commit_parked(self, handle):
        """Let the parked call through its commit, and check what it reports."""
        self._server_state = _UNREAD
        hypothesis.event(
            f'commit parked {handle.statement}: wrote={handle.wrote}')
        actor = handle.actor
        elector = self._electors[actor]
        result = handle.finish(crash=False)
        self._parked = None

        assert result is handle.wrote, (
            f'parked {handle.statement} by {actor} returned {result}, '
            f'expected {handle.wrote}; {self._state()}')
        if not result:
            assert elector.fencing_token() is None, (
                f'{actor} kept token {elector.fencing_token()} after a '
                f'{handle.statement} that produced nothing')
            self._believes_leader[actor] = False
            return

        # TERM DISCRIMINATION, read off the elector: the call hands back the
        # term its own statement computed.
        assert elector.fencing_token() == handle.pending_epoch, (
            f'parked {handle.statement} by {actor} produced token '
            f'{elector.fencing_token()}, expected {handle.pending_epoch}; '
            f'{self._state()}')
        if handle.statement == 'acquire':
            self._holder = actor
            self._holder_expired = False
            self._epoch = handle.pending_epoch
        self._holder_knows_it_leads = True
        self._believes_leader[actor] = True
        self._must_step_down[actor] = False

    @stateful.rule(handle=stateful.consumes(parked))
    def rollback_parked(self, handle):
        """Stop the parked call between its write and its commit.

        The hook raises instead of returning, so the COMMIT never reaches the
        server and the exception travels the module's own failure path: out of
        ``_execute_bounded``, into ``_run_epoch_stmt``'s catch, which drops the
        fencing token and reports a lost role. Nothing the statement wrote can
        ever have been seen, so the world must be exactly as it was.
        """
        self._server_state = _UNREAD
        hypothesis.event(
            f'roll back parked {handle.statement}: wrote={handle.wrote}')
        actor = handle.actor
        before = self._backend.observe()
        result = handle.finish(crash=True)
        self._parked = None
        after = self._backend.observe()

        assert result is False, (
            f'parked {handle.statement} by {actor} returned {result} after '
            f'failing before its commit; {self._state()}')
        assert self._electors[actor].fencing_token() is None, (
            f'{actor} kept a token through a failed {handle.statement}')
        assert after == before, (
            f'a {handle.statement} that never committed moved the lease from '
            f'{before} to {after}; {self._state()}')
        self._believes_leader[actor] = False
        # An actor that was leading has now stepped down on a call that left no
        # trace, so the row goes on naming it: the same deferred handover a
        # cancelled statement leaves behind.
        self._blocked_out(actor)

    # --- invariants ---

    @stateful.invariant()
    def one_leader_and_the_server_agrees(self):
        """MUTUAL EXCLUSION, against the server rather than the model."""
        believed = sorted(
            actor for actor in self._electors
            if self._believes_leader[actor] and not self._must_step_down[actor])
        observed = self._server()
        server_leader = None
        if observed is not None and not observed[2]:
            server_leader = observed[0]

        assert len(believed) <= 1, (
            f'{believed} all believe they lead; {self._state()}')
        assert not believed or believed[0] == server_leader, (
            f'{believed[0]} believes it leads, server says {server_leader}; '
            f'{self._state()}')
        # And the leader knows it -- except where the model knows the row got
        # ahead of its holder (see ``_holder_knows_it_leads``). Suspending it
        # there rather than dropping it keeps the case it was written for: a
        # server holding the role for an actor that has stopped acting on it
        # and cannot be displaced.
        if server_leader is not None and self._holder_knows_it_leads:
            assert believed == [
                server_leader
            ], (f'server says {server_leader} leads but {believed} believe it; '
                f'{self._state()}')

    @stateful.invariant()
    def server_matches_the_model(self):
        observed = self._server()
        holder, epoch, expired = observed or (None, None, False)
        assert holder == self._holder, f'holder {holder}; {self._state()}'
        assert expired == self._holder_expired, (
            f'expired {expired}; {self._state()}')
        if self._backend.has_fencing_token and holder is not None:
            assert epoch == self._epoch, f'epoch {epoch}; {self._state()}'

    @stateful.invariant()
    def the_epoch_never_goes_backwards(self):
        """EPOCH MONOTONICITY, including across takeovers and releases."""
        if not self._backend.has_fencing_token:
            return
        observed = self._server()
        if observed is None:
            return
        epoch = observed[1]
        assert epoch >= self._max_epoch, (
            f'epoch went {self._max_epoch} -> {epoch}; {self._state()}')
        self._max_epoch = epoch

    @stateful.invariant()
    def believed_leaders_hold_distinct_tokens(self):
        """FENCING SOUNDNESS: no two claimants can present the same token.

        Checked over every actor that still believes it leads, including one a
        fault has quietly displaced -- fencing exists precisely so a resource
        can tell that actor's writes from the new leader's.
        """
        if not self._backend.has_fencing_token:
            # The other half of the contract: this backend hands out no token,
            # so a stale holder cannot be fenced and callers have only the
            # lock itself.
            for actor, elector in self._electors.items():
                assert elector.fencing_token() is None, (
                    f'{actor} produced a token on a backend without one')
            return
        tokens = [
            elector.fencing_token()
            for actor, elector in self._electors.items()
            if self._believes_leader[actor]
        ]
        assert all(token is not None for token in tokens), (
            f'a believed leader has no token: {tokens}; {self._state()}')
        assert len(set(tokens)) == len(tokens), (
            f'claimants share a token: {tokens}; {self._state()}')

    def teardown(self):
        # First, before anything that writes the lease: a parked call holds a
        # row lock, and every release below waits on it, as does the
        # table-level lock the next example's ``reset`` needs for its DELETE.
        # Ended by failing it, so the example leaves the world as it found it,
        # and ``resume`` asserts the thread is gone.
        if self._parked is not None:
            self._parked.finish(crash=True)
            self._parked = None
        for elector in self._electors.values():
            elector.release()


@pytest.fixture()
def advisory_db(lease_db, monkeypatch):
    """``lease_db``, with lock detection routed to the Postgres advisory lock.

    ``AdvisoryLockElector`` goes through ``locks.get_lock``, which picks the
    file lock unless the state database is Postgres.
    """
    monkeypatch.setattr(locks.global_user_state, 'initialize_and_get_db',
                        lambda *a, **k: lease_db)
    return lease_db


def reports_hypothesis_statistics(test):
    """Let pytest's Hypothesis plugin report this test's statistics.

    ``run_state_machine_as_test`` builds the Hypothesis test internally, so the
    plugin does not recognise the enclosing function and collects nothing for
    ``--hypothesis-show-statistics``, which is where the ``event`` calls in the
    rules are meant to show up. Hypothesis marks its own
    ``RuleBasedStateMachine.TestCase.runTest`` with the same attribute.

    The settings go on the test as well as into ``run_state_machine_as_test``:
    the plugin reads them from here when it decides which health checks to run.
    """
    test.is_hypothesis_test = True
    return hypothesis.settings(parent=_MACHINE_SETTINGS)(test)


@pytest.fixture()
def parking_db(lease_db, monkeypatch):
    """``lease_db``, with the module's per-statement bound shortened.

    Statements that contend with a parked transaction wait this out before
    failing, so the shipped 5s would dominate a run. The bound is shortened,
    never removed -- it is the mechanism under test.
    """
    monkeypatch.setattr(leader_election, '_RENEW_STATEMENT_TIMEOUT_MS',
                        _PARKED_STATEMENT_TIMEOUT_MS)
    return lease_db


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_a_second_writer_on_the_lease_row_hits_the_statement_timeout(
        parking_db):
    """Why the machine parks at most one call at a time.

    Both lease statements write the one row for a ``lock_id``, so a second call
    in flight waits on the first. It is cancelled at the statement timeout
    before it can reach its own commit, which is the point it would have parked
    at -- so it can never park, and it never quietly succeeds either.
    """
    backend = _LeaseBackend(parking_db, 'second-writer-lock')
    backend.reset()
    a = leader_election.PgLeaseElector('second-writer-lock',
                                       holder='a',
                                       ttl_seconds=_MACHINE_TTL_SECONDS)
    b = leader_election.PgLeaseElector('second-writer-lock',
                                       holder='b',
                                       ttl_seconds=_MACHINE_TTL_SECONDS)

    first = backend.start_parked(a,
                                 'acquire',
                                 'a',
                                 wrote=True,
                                 pending_epoch=1,
                                 row_existed=False)
    try:
        assert first.wait_until_parked()
        # a's insert has not committed, so as far as any other connection can
        # see the lease is still free...
        assert backend.observe() is None

        # ...but b cannot get as far as its own commit to find that out.
        second = backend.start_parked(b,
                                      'acquire',
                                      'b',
                                      wrote=True,
                                      pending_epoch=1,
                                      row_existed=False)
        assert second.finish(crash=False) is False
        assert second.reached_its_commit() is False
        assert b.fencing_token() is None
    finally:
        first.finish(crash=True)
    assert backend.observe() is None


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_a_parked_renew_commits_and_keeps_its_term(parking_db):
    """A renew held at its own commit still confirms the same term when freed.

    The machine only reaches this by drawing a renew from the sitting leader
    and then drawing the commit rather than the failure, which is a thin slice
    of its search, so the assertion is pinned here as well.
    """
    backend = _LeaseBackend(parking_db, 'parked-renew-lock')
    backend.reset()
    a = leader_election.PgLeaseElector('parked-renew-lock',
                                       holder='a',
                                       ttl_seconds=_MACHINE_TTL_SECONDS)
    assert a.try_acquire() is True
    assert a.fencing_token() == 1

    parked = backend.start_parked(a,
                                  'renew',
                                  'a',
                                  wrote=True,
                                  pending_epoch=1,
                                  row_existed=True)
    assert parked.wait_until_parked()
    assert parked.finish(crash=False) is True
    assert a.fencing_token() == 1
    assert backend.observe() == ('a', 1, False)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_release_swallows_a_cancelled_expiry(parking_db):
    """A release whose expiry is cancelled steps down without raising.

    The machine reaches this only by drawing a park and then a release from the
    sitting leader, under one percent of its corpus, so the path is pinned here
    rather than left to whichever seed a run happens to draw.
    """
    backend = _LeaseBackend(parking_db, 'cancelled-expiry-lock')
    backend.reset()
    a = leader_election.PgLeaseElector('cancelled-expiry-lock',
                                       holder='a',
                                       ttl_seconds=_MACHINE_TTL_SECONDS)
    assert a.try_acquire() is True
    assert a.fencing_token() == 1

    # b's bid is refused, since a holds a valid lease, but the upsert still
    # locks the row it conflicted with -- so a's own expiry has to wait on it.
    b = leader_election.PgLeaseElector('cancelled-expiry-lock',
                                       holder='b',
                                       ttl_seconds=_MACHINE_TTL_SECONDS)
    parked = backend.start_parked(b,
                                  'acquire',
                                  'b',
                                  wrote=False,
                                  pending_epoch=2,
                                  row_existed=True)
    try:
        assert parked.wait_until_parked()
        # Cancelled at the statement timeout and swallowed: nothing reaches the
        # caller, and the token goes even though the row does not.
        assert a.release() is None
        assert a.fencing_token() is None
        # The expiry never landed, so the lease still names a and is still
        # live. The handover waits out the TTL instead, which is exactly what
        # release gives up on failure.
        assert backend.observe() == ('a', 1, False)
    finally:
        parked.finish(crash=True)
    assert backend.observe() == ('a', 1, False)


# --- Block, then proceed: what a waiter sees when the blocker resolves ------
#
# Everywhere else in this file a blocked contender dies at the statement
# timeout, because the machine's one thread is inside the blocked call and
# nothing can free the blocker until that call returns. A fleet has the other
# exit: the blocker commits or aborts while the waiter is still waiting, and
# under READ COMMITTED the server re-evaluates the waiter's WHERE against the
# row version that just landed before letting it through. These pin that
# re-evaluation of the lease statements, which nothing else here reaches.
#
# They are deterministic for the same reason the parked cells are: the row lock
# is the synchronisation. The waiter provably cannot proceed until the blocker's
# transaction resolves, and resolving it is an explicit call from the test --
# nothing settles, nothing polls.
#
# The waiter needs a thread of its own because it blocks, and the main thread
# has to stay free to free the blocker. It is armed in the parker only so the
# test can watch it reach its own commit; it is never held there.

# Long enough that a waiter which was never going to block would have finished
# and been caught. It cannot expire a real wait: a genuinely blocked waiter
# stays blocked until the test frees the blocker, however long that takes.
_BLOCKED_GRACE_SECONDS = 0.5


def _assert_still_blocked(waiter) -> None:
    """Fail unless the waiter really is stuck behind the blocker.

    Load-bearing rather than defensive: if the waiter had already run to
    completion, the cell would have degenerated into two calls in sequence and
    would say nothing about what a waiter sees on being let through.
    """
    time.sleep(_BLOCKED_GRACE_SECONDS)
    assert waiter.is_running(), (
        f'{waiter.label} finished instead of blocking; this cell proves '
        'nothing unless the waiter is still waiting')
    assert not waiter.reached_its_commit(), (
        f'{waiter.label} reached its own commit instead of blocking')


def _waiter_setup(db, lock_id):
    """A backend and two electors contending for *lock_id*."""
    backend = _LeaseBackend(db, lock_id)
    backend.reset()
    holder = leader_election.PgLeaseElector(lock_id,
                                            holder='a',
                                            ttl_seconds=_MACHINE_TTL_SECONDS)
    waiter = leader_election.PgLeaseElector(lock_id,
                                            holder='b',
                                            ttl_seconds=_MACHINE_TTL_SECONDS)
    return backend, holder, waiter


# These take ``lease_db`` rather than ``parking_db`` on purpose: the waiter has
# to SURVIVE its block window, and ``parking_db`` shortens the module's
# statement bound to 250ms so blocked contenders die fast. Racing that against
# the release would be flake by construction. The shipped 5s bound is what
# makes the wait safe.
@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_waiter_is_refused_by_the_lease_the_blocker_committed(lease_db):
    """A waiter let through onto a fresh valid lease loses its bid.

    Both are inserting the first row, so the waiter waits on the unique index
    rather than on a row lock. When the insert it collided with commits, its
    upsert is re-evaluated against a lease that now exists and is live, so the
    DO UPDATE WHERE is false and it takes nothing.
    """
    backend, holder, waiter = _waiter_setup(lease_db, 'waiter-refused-lock')

    blocker = backend.start_parked(holder,
                                   'acquire',
                                   'a',
                                   wrote=True,
                                   pending_epoch=1,
                                   row_existed=False)
    assert blocker.wait_until_parked()
    contender = backend.start_in_flight(waiter,
                                        'acquire',
                                        'acquire by b',
                                        hold=False)
    _assert_still_blocked(contender)
    # Nothing is committed yet, so the lease still looks free from outside.
    assert backend.observe() is None

    assert blocker.finish(crash=False) is True
    assert contender.finish() is False
    assert waiter.fencing_token() is None
    assert backend.observe() == ('a', 1, False)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_waiter_wins_the_lease_the_blocker_rolled_back(lease_db):
    """A waiter let through onto an empty table takes the first term.

    The insert it was waiting on never landed, so its own upsert is
    re-evaluated against a table with no row at all: it inserts, and the term
    it gets is the first one, not one numbered around the write that vanished.
    """
    backend, holder, waiter = _waiter_setup(lease_db, 'waiter-wins-lock')

    blocker = backend.start_parked(holder,
                                   'acquire',
                                   'a',
                                   wrote=True,
                                   pending_epoch=1,
                                   row_existed=False)
    assert blocker.wait_until_parked()
    contender = backend.start_in_flight(waiter,
                                        'acquire',
                                        'acquire by b',
                                        hold=False)
    _assert_still_blocked(contender)

    assert blocker.finish(crash=True) is False
    assert holder.fencing_token() is None
    assert contender.finish() is True
    assert waiter.fencing_token() == 1
    assert backend.observe() == ('b', 1, False)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_waiter_takes_over_a_released_lease_with_a_fresh_epoch(lease_db):
    """A waiter let through by a release takes over on a new term.

    The release expires the row while the waiter is already waiting on it. The
    waiter's upsert is then re-evaluated against that expired version: its
    WHERE passes on ``expires_at < now()``, and the epoch CASE, re-evaluated
    against the same version, has to hand out the next term rather than repeat
    the one being stepped down from -- otherwise the outgoing leader's writes
    could not be told from the incoming leader's.
    """
    backend, holder, waiter = _waiter_setup(lease_db, 'waiter-takeover-lock')
    assert holder.try_acquire() is True
    assert holder.fencing_token() == 1

    blocker = backend.start_in_flight(holder,
                                      'release',
                                      'release by a',
                                      hold=True)
    assert blocker.wait_until_parked()
    contender = backend.start_in_flight(waiter,
                                        'acquire',
                                        'acquire by b',
                                        hold=False)
    _assert_still_blocked(contender)
    # The expiry has not committed, so a is still the live holder outside.
    assert backend.observe() == ('a', 1, False)

    assert blocker.finish(crash=False) is None
    assert contender.finish() is True
    assert waiter.fencing_token() == 2
    assert backend.observe() == ('b', 2, False)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_pg_waiter_is_refused_by_a_lease_the_blocker_extended(lease_db):
    """A waiter let through by a renew finds the lease still held.

    The leader pushes its expiry out from under the waiter. Re-evaluated
    against the extended row, the waiter's WHERE fails on both arms and the
    term is untouched.
    """
    backend, holder, waiter = _waiter_setup(lease_db, 'waiter-extended-lock')
    assert holder.try_acquire() is True

    blocker = backend.start_parked(holder,
                                   'renew',
                                   'a',
                                   wrote=True,
                                   pending_epoch=1,
                                   row_existed=True)
    assert blocker.wait_until_parked()
    contender = backend.start_in_flight(waiter,
                                        'acquire',
                                        'acquire by b',
                                        hold=False)
    _assert_still_blocked(contender)

    assert blocker.finish(crash=False) is True
    assert holder.fencing_token() == 1
    assert contender.finish() is False
    assert waiter.fencing_token() is None
    assert backend.observe() == ('a', 1, False)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
@reports_hypothesis_statistics
def test_lease_election_invariants_hold_under_contention(parking_db):
    backend = _LeaseBackend(parking_db, 'machine-lock')
    stateful.run_state_machine_as_test(lambda: LeaderElectionMachine(backend),
                                       settings=_MACHINE_SETTINGS)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
@reports_hypothesis_statistics
def test_advisory_election_invariants_hold_under_contention(advisory_db):
    backend = _AdvisoryBackend(advisory_db, 'machine-advisory-lock')
    stateful.run_state_machine_as_test(lambda: LeaderElectionMachine(backend),
                                       settings=_MACHINE_SETTINGS)


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
def test_advisory_reacquire_while_leading_confirms_leadership(advisory_db):
    """The holder's own bid confirms the role instead of contending with it.

    A fresh bid opens its own session, and an exclusive advisory lock admits
    one, so an unconditional re-bid would be refused by the very lock the
    caller is holding -- ``False``, to the leader, with the role unmoved and
    nothing left that would ever release it. ``try_acquire`` therefore
    confirms a held session first ("True iff this candidate leads", matching
    the lease backend's answer in this position), and opens a fresh bid only
    once a dead session has been dropped.
    """
    a = leader_election.AdvisoryLockElector('reacquire-lock')
    assert a.try_acquire() is True
    assert a.try_acquire() is True
    assert a.renew() is True
    assert a._lock.is_locked() is True

    b = leader_election.AdvisoryLockElector('reacquire-lock')
    assert b.try_acquire() is False

    # A dead session is not blindly confirmed: reap it, and the holder's next
    # bid drops the dead lock and contends fresh for the freed role.
    key = locks.PostgresLock('reacquire-lock')._lock_key
    with advisory_db.connect() as connection:
        pid = connection.execute(
            sqlalchemy.text(
                'SELECT l.pid FROM pg_locks l WHERE l.locktype = '
                "'advisory' "
                'AND l.granted '
                'AND ((l.classid::bigint << 32) | l.objid::bigint) = :key'), {
                    'key': key
                }).scalar()
        connection.execute(sqlalchemy.text('SELECT pg_terminate_backend(:p)'),
                           {'p': pid})
        connection.commit()
    assert a.renew() is False
    assert a.try_acquire() is True
    a.release()
    assert b.try_acquire() is True
    b.release()

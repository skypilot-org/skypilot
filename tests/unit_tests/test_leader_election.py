"""Unit tests for sky.utils.leader_election.

These cover the backend-selection logic and the advisory backend on the local
file-lock path (no Postgres required). Where a Postgres fixture is available
they also cover the lease backend's SQL, and drive both backends through a
property-based state machine of several contenders for one lock.
"""
import os
import re
from typing import Optional, Tuple
from unittest import mock

import hypothesis
from hypothesis import stateful
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
# time against a real Postgres -- no threads, no waiting out a TTL, and a
# failing run replays exactly as printed.
#
# Both backends implement the same ``LeaderElector`` contract, so one machine
# drives both. They differ in three places, and only those live in the
# adapters below; every invariant is shared.

# A lease TTL far longer than a run, so a lease only ever ends because the
# machine ended it: expiry is a rule, never a race with the wall clock.
_MACHINE_TTL_SECONDS = 300.0

# Enough contenders for a takeover chain: hand the role from one to a second
# while a third contends for it.
_MACHINE_ACTORS = 3

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


class _LeaseBackend:
    """Adapter for :class:`leader_election.PgLeaseElector`."""

    # The upsert matches on ``holder``, so the current holder re-bidding for
    # its own still-valid lease is served the same row back.
    holder_can_reacquire = True
    has_fencing_token = True
    # Losing the role leaves the row in place, named but expired. That is what
    # lets a same-holder re-acquire be told apart from a first acquire.
    loss_keeps_holder = True

    def __init__(self, engine, lock_id):
        self._engine = engine
        self._lock_id = lock_id

    def reset(self) -> None:
        with self._engine.connect() as conn:
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


class _AdvisoryBackend:
    """Adapter for :class:`leader_election.AdvisoryLockElector` on Postgres."""

    # Each bid opens its own session and an exclusive advisory lock admits one
    # session, so the holder re-bidding is refused by the lock it is already
    # holding -- and keeps holding it. See
    # ``test_advisory_reacquire_while_leading_is_refused``.
    holder_can_reacquire = False
    has_fencing_token = False
    # A reaped or released session frees the lock outright; the server keeps
    # no record of who held it.
    loss_keeps_holder = False

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
        self._joined = 0
        self._server_state = _UNREAD

    def _server(self) -> Optional[Tuple[str, Optional[int], bool]]:
        """One reading of the server per step, shared by the invariants.

        Only a rule moves the election, and each rule marks this stale on the
        way in, so the invariants can all read the same snapshot instead of
        each paying for its own round trip.
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
        self._server_state = _UNREAD
        elector = self._electors[actor]
        leader = self._leader()
        if leader == actor and not self._backend.holder_can_reacquire:
            # Left out of the machine on this backend: the bid answers False
            # while the role stays put, so the actor's belief and the server's
            # state come apart and no interleaving can put them back together.
            # Pinned on its own by
            # ``test_advisory_reacquire_while_leading_is_refused``.
            hypothesis.event('acquire: self-bid, not modelled')
            return
        if leader is None:
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
        self._believes_leader[actor] = True
        self._must_step_down[actor] = False
        self._backend.note_acquired(actor, elector)

    @stateful.rule(actor=actors)
    def renew(self, actor):
        self._server_state = _UNREAD
        elector = self._electors[actor]
        expected = self._leader() == actor
        hypothesis.event(f'renew -> {expected}')

        result = elector.renew()
        assert result is expected, (f'renew by {actor}: got {result}, '
                                    f'expected {expected}; {self._state()}')
        if result and self._backend.has_fencing_token:
            # A renew confirms the running term; it never starts a new one.
            assert elector.fencing_token() == self._epoch, (
                f'renew by {actor} moved the token to '
                f'{elector.fencing_token()}, expected {self._epoch}')
        self._believes_leader[actor] = result
        self._must_step_down[actor] = False

    @stateful.rule(actor=actors)
    def release(self, actor):
        self._server_state = _UNREAD
        elector = self._electors[actor]
        was_leader = self._leader() == actor
        hypothesis.event(f'release: leading={was_leader}')

        # Documented as safe whether or not this actor leads, so an exception
        # here is itself the failure.
        elector.release()
        if self._backend.has_fencing_token:
            assert elector.fencing_token() is None, (
                f'{actor} kept token {elector.fencing_token()} after release')
        if self._holder == actor:
            self._lose_role()
        self._believes_leader[actor] = False
        self._must_step_down[actor] = False

    @stateful.precondition(lambda self: self._leader() is not None)
    @stateful.rule()
    def fault(self):
        """Take the role away without telling the holder."""
        self._server_state = _UNREAD
        holder = self._leader()
        hypothesis.event('fault')
        self._backend.fault()
        self._lose_role()
        self._must_step_down[holder] = True

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
        expected = [] if server_leader is None else [server_leader]
        assert believed == expected, (
            f'{believed} believe they lead, server says {expected}; '
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


@pg_only
@pytest.mark.xdist_group('leader_election_lease')
@reports_hypothesis_statistics
def test_lease_election_invariants_hold_under_contention(lease_db):
    backend = _LeaseBackend(lease_db, 'machine-lock')
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
def test_advisory_reacquire_while_leading_is_refused(advisory_db):
    """The holder's own bid loses to its own lock, and it keeps the lock.

    Each ``try_acquire`` opens a fresh session (a prior term's connection is
    never reused), and an exclusive advisory lock admits one session, so the
    holder's second bid is refused by the lock it is already holding. The role
    does not move: ``renew`` still reports leadership, the lock stays held, and
    no other candidate can take it. So the ``False`` here does not mean what it
    means everywhere else -- the lease backend answers True in this position --
    which is why the state machine leaves this transition to this test.
    """
    a = leader_election.AdvisoryLockElector('reacquire-lock')
    assert a.try_acquire() is True
    assert a.try_acquire() is False
    assert a.renew() is True
    assert a._lock.is_locked() is True

    b = leader_election.AdvisoryLockElector('reacquire-lock')
    assert b.try_acquire() is False
    a.release()
    assert b.try_acquire() is True
    b.release()

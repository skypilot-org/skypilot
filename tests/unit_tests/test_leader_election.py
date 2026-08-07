"""Unit tests for sky.utils.leader_election.

These cover the backend-selection logic and the advisory backend on the local
file-lock path (no Postgres required). The lease backend's SQL semantics are
exercised where a Postgres fixture is available.
"""
import os
import re
from unittest import mock

import pytest
import sqlalchemy

from sky import global_user_state
from sky.utils import leader_election


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


def test_lease_statements_use_the_dedicated_unpooled_engine(monkeypatch):
    """Every lease statement must ask for the direct, no_pool engine — the
    heartbeat may never ride the shared application engine or a transaction
    pooler, where a pool starved by slow application queries would fail
    renewals (and churn the leader) exactly when the DB is under pressure."""
    calls = []

    def record_get_engine(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('no db in this test')

    monkeypatch.setattr(leader_election.db_utils, 'get_engine',
                        record_get_engine)
    e = leader_election.PgLeaseElector('lock', holder='a')
    # Errors are swallowed into "not leading"; the engine request is the point.
    assert e.try_acquire() is False
    assert e.renew() is False
    e.release()
    assert len(calls) == 3
    for kwargs in calls:
        assert kwargs.get('direct') is True
        assert kwargs.get('no_pool') is True


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
    engine = sqlalchemy.create_engine(_PG_URL)
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

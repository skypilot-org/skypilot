"""Tests for the orphan-prune (`prune_stuck_inactive_jobs`).

A managed-job row whose creator died after `set_job_info_without_job_id`
+ `set_pending` but before `scheduler_set_waiting` is stuck in
`(schedule_state=INACTIVE, controller_pid=NULL, dag_yaml_content=NULL)`
forever — neither recovery path acts on it. The prune detects this
signature, gated by an age threshold so it cannot false-positive on a
legitimate slow launch, and transitions the row to
`(spot.status=FAILED_PRESUBMIT, schedule_state=DONE)`.

Tests are parameterized to run against BOTH SQLite (in-memory) and
Postgres (via testcontainers). The candidate-query SQL relies on
subqueries and NOT-IN semantics that have subtle dialect differences,
so exercising both prevents regressions. PG tests are auto-skipped
if Docker or testcontainers aren't available, so this still runs on
developer laptops without extra setup.
"""

import contextlib
import time
from unittest import mock

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.jobs import utils as managed_job_utils

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TESTCONTAINERS = True
except ImportError:
    _HAS_TESTCONTAINERS = False


# Module-scoped PG container — we only pay the boot cost once.
@pytest.fixture(scope='module')
def _pg_container():
    if not _HAS_TESTCONTAINERS:
        pytest.skip('testcontainers[postgres] not installed')
    try:
        with PostgresContainer('postgres:16-alpine') as pg:
            yield pg
    except Exception as e:  # pylint: disable=broad-except
        pytest.skip(f'Postgres testcontainer unavailable: {e}')


def _make_sqlite_engine(tmp_path):
    db_path = tmp_path / 'managed_jobs_prune.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})
    return engine, async_engine


def _make_pg_engine(container):
    url = container.get_connection_url()
    sync_url = url.replace('postgresql+psycopg2://', 'postgresql://')
    async_url = sync_url.replace('postgresql://', 'postgresql+asyncpg://')
    engine = create_engine(sync_url)
    async_engine = create_async_engine(async_url)
    return engine, async_engine


def _pg_reset_schema(engine):
    """Wipe ALL tables — including alembic_version — so the next
    `safe_alembic_upgrade` re-runs migrations from scratch."""
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text(
                'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'))
        conn.commit()


@pytest.fixture(params=['sqlite', 'postgres'])
def _mock_managed_jobs_db_conn(request, tmp_path, monkeypatch):
    """Fresh managed-jobs DB per test, against either SQLite or Postgres.

    For Postgres: re-uses the module-scoped container but resets the
    whole `public` schema between tests. We can't just
    `Base.metadata.drop_all` because alembic's `alembic_version` table
    isn't registered in `Base.metadata`, so dropping only the registered
    tables would leave alembic thinking migrations are already applied
    and skip re-creating the schema for the next test.
    """
    if request.param == 'sqlite':
        engine, async_engine = _make_sqlite_engine(tmp_path)
    else:
        container = request.getfixturevalue('_pg_container')
        engine, async_engine = _make_pg_engine(container)
        _pg_reset_schema(engine)

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    try:
        yield engine
    finally:
        if request.param == 'postgres':
            _pg_reset_schema(engine)


def _seed_pending(engine, *, name: str = 'orphan') -> int:
    """Insert a (job_info, spot) pair in the post-`set_pending` state."""
    job_id = state.set_job_info_without_job_id(
        name=name,
        workspace='ws',
        entrypoint='ep',
        pool=None,
        pool_hash=None,
        user_hash='u',
    )
    state.set_pending(job_id, 0, 'task0', '{}', '{}')
    return job_id


def _backdate_submitted_at(engine, job_id: int, age_seconds: float) -> None:
    """Make the spot row appear `age_seconds` old."""
    target = time.time() - age_seconds
    with orm.Session(engine) as session, session.begin():
        session.query(state.spot_table).filter(
            state.spot_table.c.spot_job_id == job_id).update(
                {state.spot_table.c.submitted_at: target})


def _read_row(engine, job_id: int):
    with orm.Session(engine) as session:
        job_info = session.execute(
            sqlalchemy.select(
                state.job_info_table.c.schedule_state,
                state.job_info_table.c.dag_yaml_content,
            ).where(state.job_info_table.c.spot_job_id == job_id)).fetchone()
        spot = session.execute(
            sqlalchemy.select(
                state.spot_table.c.status,
                state.spot_table.c.failure_reason,
                state.spot_table.c.end_at,
                state.spot_table.c.submitted_at,
            ).where(state.spot_table.c.spot_job_id == job_id)).fetchone()
    return job_info, spot


class TestPruneStuckInactiveJobs:

    def test_stuck_row_is_pruned(self, _mock_managed_jobs_db_conn):
        """A row stuck in (INACTIVE, null pid, null yaml) past the
        threshold is transitioned to (FAILED_PRESUBMIT, DONE)."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='stuck')
        _backdate_submitted_at(engine, job_id, age_seconds=7200)  # 2h

        managed_job_utils.prune_stuck_inactive_jobs()

        job_info, spot = _read_row(engine, job_id)
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.DONE.value)
        assert spot.status == state.ManagedJobStatus.FAILED_PRESUBMIT.value
        assert spot.failure_reason
        assert spot.end_at is not None

    def test_young_row_is_not_pruned(self, _mock_managed_jobs_db_conn):
        """A row that has been stuck only briefly must not be pruned —
        this is the false-positive guard for healthy in-flight launches."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='young')
        # submitted_at is "now" via set_job_info_without_job_id.

        managed_job_utils.prune_stuck_inactive_jobs()

        job_info, spot = _read_row(engine, job_id)
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.INACTIVE.value)
        assert spot.status == state.ManagedJobStatus.PENDING.value

    def test_row_with_yaml_content_is_not_pruned(self,
                                                 _mock_managed_jobs_db_conn):
        """If `scheduler_set_waiting` already committed (yaml content is
        set), the row is no longer an orphan even if schedule_state were
        somehow rewound — never prune."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='committed')
        _backdate_submitted_at(engine, job_id, age_seconds=7200)
        # Simulate a partial commit: schedule_state still INACTIVE
        # (unrealistic, but worst case for the prune) but content is set.
        with orm.Session(engine) as session, session.begin():
            session.query(state.job_info_table).filter(
                state.job_info_table.c.spot_job_id == job_id).update(
                    {state.job_info_table.c.dag_yaml_content: 'name: x'})

        managed_job_utils.prune_stuck_inactive_jobs()

        job_info, _ = _read_row(engine, job_id)
        # NOT pruned.
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.INACTIVE.value)

    def test_row_with_controller_pid_is_not_pruned(self,
                                                   _mock_managed_jobs_db_conn):
        """A row with a controller PID is owned by `update_managed_jobs_statuses`."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='has-pid')
        _backdate_submitted_at(engine, job_id, age_seconds=7200)
        with orm.Session(engine) as session, session.begin():
            session.query(state.job_info_table).filter(
                state.job_info_table.c.spot_job_id == job_id).update(
                    {state.job_info_table.c.controller_pid: 12345})

        managed_job_utils.prune_stuck_inactive_jobs()

        job_info, _ = _read_row(engine, job_id)
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.INACTIVE.value)

    def test_waiting_row_is_not_pruned(self, _mock_managed_jobs_db_conn):
        """A row that has progressed past INACTIVE is owned by the
        scheduler / recovery — prune should never see it."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='waiting')
        _backdate_submitted_at(engine, job_id, age_seconds=7200)
        state.scheduler_set_waiting([job_id], 'dag', 'user-dag', 'env', None,
                                    100)

        managed_job_utils.prune_stuck_inactive_jobs()

        job_info, _ = _read_row(engine, job_id)
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.WAITING.value)

    def test_cancelled_row_is_not_pruned(self, _mock_managed_jobs_db_conn):
        """User cancel via `set_pending_cancelled` should be preserved —
        the candidate query excludes jobs whose spot status is no longer
        PENDING."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='user-cancelled')
        _backdate_submitted_at(engine, job_id, age_seconds=7200)
        assert state.set_pending_cancelled(job_id) is True

        managed_job_utils.prune_stuck_inactive_jobs()

        _, spot = _read_row(engine, job_id)
        # Cancel preserved; prune did NOT bulldoze to FAILED_PRESUBMIT.
        assert spot.status == state.ManagedJobStatus.CANCELLED.value

    def test_race_with_scheduler_set_waiting(self, _mock_managed_jobs_db_conn):
        """The candidate scan picks up a stuck row, but
        `scheduler_set_waiting` commits between the scan and the
        per-row UPDATE. The atomic conditional UPDATE inside
        `fail_if_still_stuck_inactive` must no-op (the legitimate
        launch wins)."""
        engine = _mock_managed_jobs_db_conn
        job_id = _seed_pending(engine, name='race')
        _backdate_submitted_at(engine, job_id, age_seconds=7200)

        # Simulate the race by replacing `find_stuck_inactive_jobs` to
        # return our candidate while we let `scheduler_set_waiting`
        # commit just before the per-row UPDATE runs.
        real_find = state.find_stuck_inactive_jobs

        def _racing_find(cutoff_ts):
            ids = real_find(cutoff_ts)
            if job_id in ids:
                # The scheduler beats us to it.
                state.scheduler_set_waiting([job_id], 'dag', 'user-dag', 'env',
                                            None, 100)
            return ids

        with mock.patch.object(state, 'find_stuck_inactive_jobs', _racing_find):
            managed_job_utils.prune_stuck_inactive_jobs()

        job_info, spot = _read_row(engine, job_id)
        # The scheduler's transition is preserved.
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.WAITING.value)
        # The spot row is NOT bulldozed.
        assert spot.status == state.ManagedJobStatus.PENDING.value

    def test_non_consolidation_orphan_is_pruned(self,
                                                _mock_managed_jobs_db_conn):
        """In non-consolidation mode, the orphan signature is identical
        and the prune must still act on it. The threshold default is
        sized for the worst legitimate non-consolidation healthy-launch
        dwell time, so the prune is mode-agnostic."""
        engine = _mock_managed_jobs_db_conn
        # Inserting via the legacy codegen path simulates the non-
        # consolidation flow where `set_job_info` writes a row that
        # later (in a separate code path) gets PENDING-ed.
        state.set_job_info(job_id=999,
                           name='legacy-orphan',
                           workspace='ws',
                           entrypoint='ep',
                           pool=None,
                           pool_hash=None,
                           user_hash='u')
        state.set_pending(999, 0, 'task0', '{}', '{}')
        _backdate_submitted_at(engine, 999, age_seconds=7200)

        managed_job_utils.prune_stuck_inactive_jobs()

        job_info, spot = _read_row(engine, 999)
        assert job_info.schedule_state == (
            state.ManagedJobScheduleState.DONE.value)
        assert spot.status == state.ManagedJobStatus.FAILED_PRESUBMIT.value

    def test_failed_presubmit_is_terminal_and_failed(self):
        """FAILED_PRESUBMIT must be in both terminal_statuses and
        failure_statuses so the rest of the system treats it correctly."""
        assert state.ManagedJobStatus.FAILED_PRESUBMIT in (
            state.ManagedJobStatus.terminal_statuses())
        assert state.ManagedJobStatus.FAILED_PRESUBMIT in (
            state.ManagedJobStatus.failure_statuses())
        assert state.ManagedJobStatus.FAILED_PRESUBMIT.is_terminal()
        assert state.ManagedJobStatus.FAILED_PRESUBMIT.is_failed()

    def test_submitted_at_stamped_on_set_pending(self,
                                                 _mock_managed_jobs_db_conn):
        """`spot.submitted_at` is stamped at `set_pending` time so a
        row that gets stuck in PENDING+INACTIVE has age info. (Today's
        controller-side `set_starting_async` still overwrites it later
        with the controller-start time for jobs that progress past
        PENDING — that pre-existing behaviour is unchanged.)"""
        engine = _mock_managed_jobs_db_conn
        before = time.time()
        job_id = state.set_job_info_without_job_id(name='ts',
                                                   workspace='ws',
                                                   entrypoint='ep',
                                                   pool=None,
                                                   pool_hash=None,
                                                   user_hash='u')
        state.set_pending(job_id, 0, 'task0', '{}', '{}')
        after = time.time()

        with orm.Session(engine) as session:
            row = session.execute(
                sqlalchemy.select(state.spot_table.c.submitted_at).where(
                    state.spot_table.c.spot_job_id == job_id)).fetchone()
        assert row.submitted_at is not None
        assert before <= row.submitted_at <= after

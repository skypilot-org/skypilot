"""The daemon that turns finished launch attempts into phase metrics."""
import asyncio
import types
from unittest import mock

import sqlalchemy

from sky.jobs import state as managed_job_state
from sky.metrics import utils as metrics_lib
from sky.server import daemons
from sky.skylet import constants as skylet_constants
from sky.utils.db import db_utils


def _attempt(attempt_id='a1'):
    return types.SimpleNamespace(attempt_id=attempt_id,
                                 provision_start=100.0,
                                 instances_requested=110.0,
                                 admitted=None,
                                 instances_ready=160.0,
                                 outcome='succeeded',
                                 workspace='eng')


def test_daemon_is_skipped_when_metrics_are_disabled(monkeypatch):
    """Claiming marks rows observed, so it must not happen with metrics off.

    Otherwise the attempts are consumed silently and turning metrics on later
    starts from a permanent hole.
    """
    monkeypatch.setenv('PROMETHEUS_MULTIPROC_DIR', '/tmp/metrics')
    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', False)
    assert daemons.should_skip_launch_metrics() is True

    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', True)
    assert daemons.should_skip_launch_metrics() is False


def test_daemon_is_skipped_when_its_output_would_be_invisible(monkeypatch):
    """This daemon has its own process, so without multiprocess mode nothing
    it observes reaches /metrics.

    Found by running it for real: metrics were on, the daemon claimed every
    attempt and logged success, and no series ever appeared -- because
    in-process metrics kept working, which is what makes this easy to miss.
    Claiming in that state burns the attempts for good.
    """
    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', True)
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)

    assert daemons.should_skip_launch_metrics() is True


def test_one_bad_row_does_not_block_the_others(monkeypatch):
    """A row that fails to observe must not stall the sweep.

    It stays claimed too, so a poison record cannot make the daemon spin on it
    forever.
    """
    monkeypatch.setattr(
        daemons.global_user_state, 'claim_unobserved_launch_attempts',
        lambda: [_attempt('bad'), _attempt('good')])
    monkeypatch.setattr(daemons.time, 'sleep', lambda _: None)
    monkeypatch.setattr(daemons.global_user_state,
                        'sweep_abandoned_launch_attempts', lambda: 0)
    monkeypatch.setattr(daemons, '_record_job_launch_timelines', lambda: 0)

    observed = []

    def _observe(row):
        if row.attempt_id == 'bad':
            raise ValueError('malformed row')
        observed.append(row.attempt_id)

    with mock.patch('sky.metrics.launch_phases.observe_attempt', _observe):
        daemons.launch_metrics_event()

    assert observed == ['good']


def _one_running_job(tmp_path, monkeypatch, pool=None):
    """A spot_jobs DB holding a single job that has reached RUNNING."""
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=1,
            task_id=0,
            task_name='t',
            status='RUNNING',
            created_at=100.0,
            # Task 0 is given its origin at set_pending, so a row without one
            # is not a row this path produces.
            eligible_at=100.0,
            submitted_at=110.0,
            start_at=200.0))
        session.execute(managed_job_state.job_info_table.insert().values(
            spot_job_id=1, workspace='eng', pool=pool))
        session.commit()


def test_a_task_with_no_origin_is_skipped_rather_than_remeasured(
        tmp_path, monkeypatch):
    """An absent origin must lose the task, not relocate its clock.

    Writing eligible_at is best-effort, so it can be missing -- and the task it
    goes missing on is a pipeline's later task, whose created_at is the
    submission of the whole job. Falling back to it would charge every upstream
    task's runtime to this one's controller wait, which is the distortion the
    column exists to remove. A lost sample moves no distribution; a fabricated
    one moves two.
    """
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=1,
            task_id=1,
            task_name='second',
            status='RUNNING',
            created_at=100.0,
            # The best-effort write did not land.
            eligible_at=None,
            submitted_at=7300.0,
            start_at=7400.0))
        session.execute(managed_job_state.job_info_table.insert().values(
            spot_job_id=1, workspace='eng'))
        session.commit()

    assert managed_job_state.get_jobs_pending_launch_timeline() == []


def test_a_never_ran_task_with_no_origin_is_skipped_too(tmp_path, monkeypatch):
    """The same rule on the sibling query, which the other test cannot reach.

    A task that went terminal without running is counted from its origin to its
    submission. With no origin, measuring from the job's creation charges it
    every upstream task's runtime -- and this query is the one that feeds the
    never-ran counts, so a wrong number here is not a missing bar but a wrong
    one.
    """
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=2,
            task_id=1,
            task_name='second',
            status='FAILED',
            created_at=100.0,
            eligible_at=None,
            submitted_at=7300.0,
            start_at=None,
            end_at=7350.0))
        session.execute(managed_job_state.job_info_table.insert().values(
            spot_job_id=2, workspace='eng'))
        session.commit()

    assert managed_job_state.get_jobs_that_never_ran() == []


def test_the_park_writes_nothing_for_a_row_it_cannot_measure(
        tmp_path, monkeypatch):
    """A row with no origin must stay unmeasured rather than get a made-up one.

    The park exists so a row that raises leaves the pending set instead of
    being re-selected forever -- so the tempting repair, when the origin is
    what is missing, is to park it with any number at all. That number would
    reach the stored row and the view that renders it. Better an unparked row,
    logged, than a duration nobody can trace back to a measurement.
    """
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=3,
            task_id=1,
            task_name='second',
            status='RUNNING',
            created_at=100.0,
            eligible_at=None,
            submitted_at=7300.0,
            start_at=7400.0))
        session.commit()

    # The real query excludes this row; handing it over directly is how the
    # branch is reached at all.
    monkeypatch.setattr(
        managed_job_state, 'get_jobs_pending_launch_timeline',
        lambda *a, **k: [{
            'spot_job_id': 3,
            'task_id': 1,
            'task_name': 'second',
            'created_at': 100.0,
            'eligible_at': None,
            'submitted_at': 7300.0,
            'start_at': 7400.0,
            'workspace': 'eng',
            'pool': None,
        }])

    daemons._record_job_launch_timelines()

    with sqlalchemy.orm.Session(engine) as session:
        parked = session.execute(
            sqlalchemy.select(
                managed_job_state.spot_table.c.t_time_to_running).where(
                    managed_job_state.spot_table.c.spot_job_id == 3)).scalar()
    assert parked is None, f'the park invented a duration: {parked}'


def test_the_handoff_origin_survives_its_own_session(tmp_path, monkeypatch):
    """A pipeline's handoff write must still be there after the session closes.

    The helper that runs this update opens a session and closes it; it does not
    commit, so an _op that does not commit itself is rolled back on exit. The
    write then fails silently -- nothing raises, nothing is logged, and the
    task simply has no origin. Because the timeline queries require one, the
    consequence is not a wrong number but no measurement at all.

    Reading in a FRESH session is what makes that visible: within the writing
    session an uncommitted row still reads back.
    """
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=42,
            task_id=1,
            task_name='second',
            status='PENDING',
            created_at=100.0,
            eligible_at=None))
        session.commit()

    asyncio.run(managed_job_state.set_eligible_at_async(42, 1, 7200.0))

    with sqlalchemy.orm.Session(engine) as session:
        got = session.execute(
            sqlalchemy.select(managed_job_state.spot_table.c.eligible_at).where(
                sqlalchemy.and_(
                    managed_job_state.spot_table.c.spot_job_id == 42,
                    managed_job_state.spot_table.c.task_id == 1))).scalar()

    assert got == 7200.0, (
        'the handoff origin did not survive the session; the task would be '
        'skipped by the timeline queries entirely')


def test_a_job_with_no_submission_time_is_not_broken_down(
        tmp_path, monkeypatch):
    """Every timestamp the split subtracts has to be there before it runs.

    The row is not dropped by being excluded here -- the counts come from a
    different query -- but a breakdown that raises parks a total-only timeline,
    so the job would report its whole wait as unattributed.
    """
    _one_running_job(tmp_path, monkeypatch)
    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(
            managed_job_state.spot_table.update().values(submitted_at=None))
        session.commit()

    assert managed_job_state.get_jobs_pending_launch_timeline() == []


def test_a_job_that_cannot_be_broken_down_leaves_the_pending_set(
        tmp_path, monkeypatch):
    """A row that keeps raising must not be handed back every tick.

    The pending set is `t_time_to_running IS NULL` with a LIMIT and ordered by
    the oldest first, so rows that always fail sit at the head of it: enough of
    them and no newer job is ever reached again. Parking the total alone is
    what takes the row out.
    """
    _one_running_job(tmp_path, monkeypatch)
    monkeypatch.setattr(daemons.global_user_state,
                        'get_launch_attempts_for_cluster', lambda _: [])

    with mock.patch('sky.metrics.launch_phases.compute_job_timeline',
                    side_effect=ValueError('malformed attempt')):
        assert daemons._record_job_launch_timelines() == 0

    assert managed_job_state.get_jobs_pending_launch_timeline() == []


def test_the_timeline_query_carries_the_pool(tmp_path, monkeypatch):
    """A pool job skips provisioning, so its path must be distinguishable.

    The query decides that: selecting only the workspace made every job report
    path=provision, so a pool job's missing provisioning phases read the same
    as lost data. Caught by running a real pool job, not by a unit test.
    """
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=1,
            task_id=0,
            task_name='t',
            status='RUNNING',
            created_at=100.0,
            # Task 0 is given its origin at set_pending, so a row without one
            # is not a row this path produces.
            eligible_at=100.0,
            submitted_at=110.0,
            start_at=200.0))
        session.execute(managed_job_state.job_info_table.insert().values(
            spot_job_id=1, workspace='eng', pool='warm-pool'))
        session.commit()

    rows = managed_job_state.get_jobs_pending_launch_timeline()

    assert len(rows) == 1
    assert rows[0]['pool'] == 'warm-pool'

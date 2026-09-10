"""Unit tests for the consolidation-mode HA recovery sweep.

Covers the state-layer queries the sweep is built on (against a real SQLite
state DB) and then the sweep itself, which decides per job whether to hand it
back to a controller or settle it directly.
"""
import contextlib
from unittest import mock

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import state
from sky.jobs import utils as managed_job_utils

ScheduleState = state.ManagedJobScheduleState
Status = state.ManagedJobStatus


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    """A throwaway SQLite managed-jobs state DB."""
    db_path = tmp_path / 'managed_jobs.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(section: str):
        with filelock.FileLock(str(tmp_path / f'.{section}.lock'), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


def _add_job(engine,
             job_id: int,
             schedule_state,
             *,
             task_statuses=(),
             controller_pid=None,
             controller_pid_started_at=None,
             pool=None,
             task_name='task'):
    """Insert one job_info row plus one spot row per entry in task_statuses.

    Writes the tables directly rather than going through the state-transition
    helpers: these tests are about how the sweep's queries read state, so the
    state under test has to be stated exactly, including combinations the
    normal transitions would not produce in this order.
    """
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.insert(state.job_info_table).values({
                'spot_job_id': job_id,
                'name': f'job-{job_id}',
                'schedule_state': (
                    schedule_state.value if schedule_state is not None else None
                ),
                'controller_pid': controller_pid,
                'controller_pid_started_at': controller_pid_started_at,
                'pool': pool,
            }))
        for task_id, status in enumerate(task_statuses):
            session.execute(
                sqlalchemy.insert(state.spot_table).values({
                    'spot_job_id': job_id,
                    'task_id': task_id,
                    'task_name': f'{task_name}{task_id}',
                    'job_name': f'job-{job_id}',
                    'status': status.value,
                    'run_timestamp': f'ts-{job_id}-{task_id}',
                }))
        session.commit()


def _schedule_states(engine, job_ids):
    with orm.Session(engine) as session:
        rows = session.execute(
            sqlalchemy.select(
                state.job_info_table.c.spot_job_id,
                state.job_info_table.c.schedule_state,
                state.job_info_table.c.controller_pid,
            ).where(
                state.job_info_table.c.spot_job_id.in_(job_ids))).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


# ---------------------------------------------------------------------------
# get_jobs_needing_recovery_check
# ---------------------------------------------------------------------------


def test_needing_recovery_check_skips_states_with_no_controller(jobs_db):
    """DONE / WAITING / INACTIVE jobs are not candidates for recovery."""
    _add_job(jobs_db, 1, ScheduleState.DONE)
    _add_job(jobs_db, 2, ScheduleState.WAITING)
    _add_job(jobs_db, 3, ScheduleState.INACTIVE)
    _add_job(jobs_db, 4, ScheduleState.LAUNCHING)
    _add_job(jobs_db, 5, ScheduleState.ALIVE)
    _add_job(jobs_db, 6, ScheduleState.ALIVE_BACKOFF)

    candidates = state.get_jobs_needing_recovery_check()

    assert [job['job_id'] for job in candidates] == [4, 5, 6]


def test_needing_recovery_check_includes_null_schedule_state(jobs_db):
    """Jobs predating schedule_state have no state to interpret; still check."""
    _add_job(jobs_db, 1, None)

    candidates = state.get_jobs_needing_recovery_check()

    assert [job['job_id'] for job in candidates] == [1]
    assert candidates[0]['schedule_state'] is None


def test_needing_recovery_check_returns_one_row_per_job(jobs_db):
    """A multi-task job appears once, not once per task."""
    _add_job(jobs_db,
             1,
             ScheduleState.ALIVE,
             task_statuses=(Status.SUCCEEDED, Status.RUNNING, Status.PENDING),
             controller_pid=4242,
             controller_pid_started_at=99.5)

    candidates = state.get_jobs_needing_recovery_check()

    assert candidates == [{
        'job_id': 1,
        'controller_pid': 4242,
        'controller_pid_started_at': 99.5,
        'schedule_state': ScheduleState.ALIVE,
    }]


# ---------------------------------------------------------------------------
# get_job_ids_with_all_tasks_cancelled
# ---------------------------------------------------------------------------


def test_all_tasks_cancelled_partitions_jobs(jobs_db):
    _add_job(jobs_db,
             1,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED,))
    _add_job(jobs_db,
             2,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED, Status.CANCELLED))
    # One task not cancelled: the job is not finished.
    _add_job(jobs_db,
             3,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED, Status.RUNNING))
    # Terminal but not CANCELLED: cleanup may not have run, so not eligible.
    _add_job(jobs_db,
             4,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.SUCCEEDED,))
    _add_job(jobs_db,
             5,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.FAILED_CONTROLLER,))

    assert state.get_job_ids_with_all_tasks_cancelled([1, 2, 3, 4, 5]) == {1, 2}


def test_all_tasks_cancelled_excludes_job_without_tasks(jobs_db):
    """A job_info row with no spot rows is mid-submission, not finished."""
    _add_job(jobs_db, 1, ScheduleState.LAUNCHING)

    assert state.get_job_ids_with_all_tasks_cancelled([1]) == set()


def test_all_tasks_cancelled_handles_more_ids_than_one_chunk(jobs_db):
    job_ids = list(range(1, state._RECOVERY_CHUNK_SIZE * 2 + 5))
    for job_id in job_ids:
        _add_job(jobs_db,
                 job_id,
                 ScheduleState.LAUNCHING,
                 task_statuses=(Status.CANCELLED,))

    assert state.get_job_ids_with_all_tasks_cancelled(job_ids) == set(job_ids)


def test_all_tasks_cancelled_empty_input(jobs_db):
    assert state.get_job_ids_with_all_tasks_cancelled([]) == set()


# ---------------------------------------------------------------------------
# get_non_pool_task_names
# ---------------------------------------------------------------------------


def test_non_pool_task_names_excludes_pool_jobs(jobs_db):
    _add_job(jobs_db,
             1,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED, Status.CANCELLED),
             task_name='solo')
    _add_job(jobs_db,
             2,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED,),
             pool='my-pool',
             task_name='pooled')

    assert sorted(state.get_non_pool_task_names([1, 2])) == [(1, 'solo0'),
                                                             (1, 'solo1')]


# ---------------------------------------------------------------------------
# batched writes
# ---------------------------------------------------------------------------


def test_reset_batch_sets_waiting_and_clears_pid(jobs_db):
    _add_job(jobs_db, 1, ScheduleState.LAUNCHING, controller_pid=11)
    _add_job(jobs_db, 2, ScheduleState.ALIVE, controller_pid=22)

    assert state.reset_jobs_for_recovery_batch([1, 2]) == 2

    assert _schedule_states(jobs_db, [1, 2]) == {
        1: (ScheduleState.WAITING.value, None),
        2: (ScheduleState.WAITING.value, None),
    }


def test_reset_batch_leaves_jobs_that_no_longer_need_recovery(jobs_db):
    """A job that reached DONE after the sweep read it keeps its newer state."""
    _add_job(jobs_db, 1, ScheduleState.DONE, controller_pid=11)
    _add_job(jobs_db, 2, ScheduleState.LAUNCHING, controller_pid=22)

    assert state.reset_jobs_for_recovery_batch([1, 2]) == 1

    assert _schedule_states(jobs_db, [1, 2]) == {
        1: (ScheduleState.DONE.value, 11),
        2: (ScheduleState.WAITING.value, None),
    }


def test_set_done_batch(jobs_db):
    _add_job(jobs_db, 1, ScheduleState.LAUNCHING, controller_pid=11)
    _add_job(jobs_db, 2, ScheduleState.WAITING, controller_pid=22)

    # Job 2 is WAITING, which needs no recovery, so it is left alone.
    assert state.set_jobs_done_batch([1, 2]) == 1

    assert _schedule_states(jobs_db, [1, 2]) == {
        1: (ScheduleState.DONE.value, None),
        2: (ScheduleState.WAITING.value, 22),
    }


def test_batched_writes_handle_more_ids_than_one_chunk(jobs_db):
    job_ids = list(range(1, state._RECOVERY_CHUNK_SIZE * 2 + 5))
    for job_id in job_ids:
        _add_job(jobs_db,
                 job_id,
                 ScheduleState.LAUNCHING,
                 controller_pid=job_id)

    assert state.reset_jobs_for_recovery_batch(job_ids) == len(job_ids)

    states = _schedule_states(jobs_db, job_ids)
    assert all(states[job_id] == (ScheduleState.WAITING.value, None)
               for job_id in job_ids)


def test_batched_writes_empty_input(jobs_db):
    assert state.reset_jobs_for_recovery_batch([]) == 0
    assert state.set_jobs_done_batch([]) == 0


# ---------------------------------------------------------------------------
# ha_recovery_for_consolidation_mode
# ---------------------------------------------------------------------------


@pytest.fixture
def sweep_env(jobs_db, tmp_path, monkeypatch):
    """Run the sweep against ``jobs_db`` with its side effects stubbed out."""
    monkeypatch.setattr(managed_job_utils.constants,
                        'HA_PERSISTENT_RECOVERY_LOG_PATH',
                        str(tmp_path / '{}recovery.log'))
    monkeypatch.setattr(managed_job_utils.scheduler, 'maybe_start_controllers',
                        mock.Mock())
    # No cluster rows exist unless a test says otherwise.
    existing = mock.Mock(return_value=set())
    monkeypatch.setattr(managed_job_utils.global_user_state,
                        'filter_existing_cluster_names', existing)
    throttle = mock.Mock()
    monkeypatch.setattr(managed_job_utils, '_throttle_recovery_sweep', throttle)
    yield mock.Mock(engine=jobs_db,
                    existing_clusters=existing,
                    throttle=throttle)


def test_sweep_settles_cancelled_jobs_and_recovers_the_rest(sweep_env):
    """All-cancelled jobs go straight to DONE; live jobs go back to WAITING."""
    _add_job(sweep_env.engine,
             1,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED,))
    _add_job(sweep_env.engine,
             2,
             ScheduleState.ALIVE,
             task_statuses=(Status.RUNNING,))
    _add_job(sweep_env.engine,
             3,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.SUCCEEDED,))

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert _schedule_states(sweep_env.engine, [1, 2, 3]) == {
        1: (ScheduleState.DONE.value, None),
        2: (ScheduleState.WAITING.value, None),
        3: (ScheduleState.WAITING.value, None),
    }


def test_sweep_recovers_cancelled_job_whose_cluster_survives(sweep_env):
    """A leftover cluster means cleanup must run, so use a controller."""
    _add_job(sweep_env.engine,
             1,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.CANCELLED,),
             task_name='leaky')
    cluster_name = managed_job_utils.generate_managed_job_cluster_name(
        'leaky0', 1)
    sweep_env.existing_clusters.return_value = {cluster_name}

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert _schedule_states(sweep_env.engine, [1]) == {
        1: (ScheduleState.WAITING.value, None)
    }


def test_sweep_skips_job_with_a_live_controller(sweep_env, monkeypatch):
    _add_job(sweep_env.engine,
             1,
             ScheduleState.ALIVE,
             task_statuses=(Status.RUNNING,),
             controller_pid=1234,
             controller_pid_started_at=7.0)
    monkeypatch.setattr(managed_job_utils, 'controller_process_alive',
                        mock.Mock(return_value=True))

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert _schedule_states(sweep_env.engine, [1]) == {
        1: (ScheduleState.ALIVE.value, 1234)
    }


def test_sweep_recovers_job_when_liveness_check_raises(sweep_env, monkeypatch):
    """A psutil failure must not skip recovery, nor crash the sweep."""
    _add_job(sweep_env.engine,
             1,
             ScheduleState.ALIVE,
             task_statuses=(Status.RUNNING,),
             controller_pid=1234)
    monkeypatch.setattr(managed_job_utils, 'controller_process_alive',
                        mock.Mock(side_effect=RuntimeError('psutil boom')))

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert _schedule_states(sweep_env.engine, [1]) == {
        1: (ScheduleState.WAITING.value, None)
    }


def test_sweep_processes_every_batch_and_pauses_between_them(
        sweep_env, monkeypatch):
    monkeypatch.setattr(managed_job_utils, '_RECOVERY_SWEEP_BATCH_SIZE', 2)
    job_ids = [1, 2, 3, 4, 5]
    for job_id in job_ids:
        _add_job(sweep_env.engine,
                 job_id,
                 ScheduleState.LAUNCHING,
                 task_statuses=(Status.RUNNING,))

    managed_job_utils.ha_recovery_for_consolidation_mode()

    states = _schedule_states(sweep_env.engine, job_ids)
    assert all(states[job_id] == (ScheduleState.WAITING.value, None)
               for job_id in job_ids)
    # 3 batches of 2/2/1 -> a pause before batches 2 and 3, none after the last.
    assert sweep_env.throttle.call_count == 2


def test_sweep_does_not_pause_for_a_single_batch(sweep_env):
    _add_job(sweep_env.engine,
             1,
             ScheduleState.LAUNCHING,
             task_statuses=(Status.RUNNING,))

    managed_job_utils.ha_recovery_for_consolidation_mode()

    sweep_env.throttle.assert_not_called()


def test_sweep_does_nothing_when_no_job_needs_recovery(sweep_env):
    _add_job(sweep_env.engine,
             1,
             ScheduleState.DONE,
             task_statuses=(Status.SUCCEEDED,))
    _add_job(sweep_env.engine,
             2,
             ScheduleState.WAITING,
             task_statuses=(Status.PENDING,))

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert _schedule_states(sweep_env.engine, [1, 2]) == {
        1: (ScheduleState.DONE.value, None),
        2: (ScheduleState.WAITING.value, None),
    }
    sweep_env.throttle.assert_not_called()


# ---------------------------------------------------------------------------
# _throttle_recovery_sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('batch_seconds,expected', [
    (0.0, None),
    (0.4, 0.4),
    (3.0, 3.0),
    (60.0, managed_job_utils._RECOVERY_SWEEP_MAX_PAUSE_SECONDS),
])
def test_throttle_scales_with_batch_cost_and_is_capped(monkeypatch,
                                                       batch_seconds, expected):
    sleeps = []
    monkeypatch.setattr(managed_job_utils.time, 'sleep', sleeps.append)

    managed_job_utils._throttle_recovery_sweep(batch_seconds, mock.Mock())

    assert sleeps == ([] if expected is None else [expected])

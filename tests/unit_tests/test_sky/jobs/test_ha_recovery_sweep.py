"""Unit tests for the consolidation-mode HA recovery sweep.

Covers the state-layer queries the sweep is built on (against a real SQLite
state DB) and then the sweep itself.
"""
import asyncio
import contextlib
from typing import Optional
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


def _ownership(engine, job_id):
    """(schedule_state, controller_pid, controller_pid_started_at) of a job."""
    with orm.Session(engine) as session:
        return tuple(
            session.execute(
                sqlalchemy.select(
                    state.job_info_table.c.schedule_state,
                    state.job_info_table.c.controller_pid,
                    state.job_info_table.c.controller_pid_started_at,
                ).where(state.job_info_table.c.spot_job_id == job_id)).one())


def _set_ownership(engine, job_id, *, schedule_state, controller_pid,
                   controller_pid_started_at):
    """Overwrite a job's ownership columns, as a concurrent actor would."""
    with orm.Session(engine) as session:
        session.execute(
            sqlalchemy.update(state.job_info_table).where(
                state.job_info_table.c.spot_job_id == job_id).values({
                    'schedule_state': schedule_state.value,
                    'controller_pid': controller_pid,
                    'controller_pid_started_at': controller_pid_started_at,
                }))
        session.commit()


def _add_claimable_job(engine, num_tasks: int, *, controller_pid: int,
                       controller_pid_started_at: float) -> int:
    """Create a job through the real submission path, then mark it ALIVE.

    Unlike ``_add_job``, the job has everything the production claim path
    (``get_waiting_job_async``) needs, so a test can claim it once the sweep
    resets it.
    """
    job_id = state.set_job_info_without_job_id(name='claimable',
                                               workspace='ws1',
                                               entrypoint='ep',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='user1')
    for task_id in range(num_tasks):
        state.set_pending(job_id,
                          task_id=task_id,
                          task_name=f'task{task_id}',
                          resources_str='{}',
                          metadata='{}')
    state.scheduler_set_waiting([job_id], '/tmp/dag.yaml', '/tmp/user.yaml',
                                '/tmp/env', None, 100)
    _set_ownership(engine,
                   job_id,
                   schedule_state=ScheduleState.ALIVE,
                   controller_pid=controller_pid,
                   controller_pid_started_at=controller_pid_started_at)
    return job_id


def _claim_waiting_job(pid: int, pid_started_at: float) -> Optional[int]:
    """Claim a job the way a controller does, via the production claim path.

    ``get_waiting_job_async`` is the real WAITING -> LAUNCHING compare-and-swap
    that stamps the claiming controller's pid. Returns the claimed job id, or
    None if nothing was claimable.
    """

    async def _claim():
        return await state.get_waiting_job_async(pid=pid,
                                                 pid_started_at=pid_started_at)

    loop = asyncio.new_event_loop()
    try:
        claimed = loop.run_until_complete(_claim())
    finally:
        loop.close()
    return None if claimed is None else claimed['job_id']


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
# batched writes
# ---------------------------------------------------------------------------


def test_reset_batch_sets_waiting_and_clears_pid(jobs_db):
    _add_job(jobs_db, 1, ScheduleState.LAUNCHING, controller_pid=11)
    _add_job(jobs_db, 2, ScheduleState.ALIVE, controller_pid=22)

    assert state.reset_jobs_for_recovery_batch(
        state.get_jobs_needing_recovery_check()) == [1, 2]

    assert _schedule_states(jobs_db, [1, 2]) == {
        1: (ScheduleState.WAITING.value, None),
        2: (ScheduleState.WAITING.value, None),
    }


def test_reset_batch_leaves_jobs_that_no_longer_need_recovery(jobs_db):
    """A job that reached DONE after the sweep read it keeps its newer state,
    even though its pid columns did not change."""
    _add_job(jobs_db, 1, ScheduleState.ALIVE, controller_pid=11)
    _add_job(jobs_db, 2, ScheduleState.LAUNCHING, controller_pid=22)
    observed = state.get_jobs_needing_recovery_check()
    _set_ownership(jobs_db,
                   1,
                   schedule_state=ScheduleState.DONE,
                   controller_pid=11,
                   controller_pid_started_at=None)

    assert state.reset_jobs_for_recovery_batch(observed) == [2]

    assert _schedule_states(jobs_db, [1, 2]) == {
        1: (ScheduleState.DONE.value, 11),
        2: (ScheduleState.WAITING.value, None),
    }


def test_reset_batch_keeps_a_claim_made_after_the_read(jobs_db):
    """The race the compare-and-swap exists for: a controller claimed the job
    (stamping its own pid) after the sweep read it. Resetting it would orphan
    the fresh claim and hand the job to a second controller."""
    _add_job(jobs_db,
             1,
             ScheduleState.ALIVE,
             controller_pid=100,
             controller_pid_started_at=1.0)
    observed = state.get_jobs_needing_recovery_check()
    _set_ownership(jobs_db,
                   1,
                   schedule_state=ScheduleState.LAUNCHING,
                   controller_pid=200,
                   controller_pid_started_at=2.0)

    assert state.reset_jobs_for_recovery_batch(observed) == []

    assert _ownership(jobs_db, 1) == (ScheduleState.LAUNCHING.value, 200, 2.0)


def test_reset_batch_compares_the_pid_start_time(jobs_db):
    """A pid alone can be reused; the start time tells the processes apart."""
    _add_job(jobs_db,
             1,
             ScheduleState.ALIVE,
             controller_pid=100,
             controller_pid_started_at=1.0)
    stale = [{
        'job_id': 1,
        'controller_pid': 100,
        'controller_pid_started_at': 0.5,
        'schedule_state': ScheduleState.ALIVE,
    }]

    assert state.reset_jobs_for_recovery_batch(stale) == []

    assert _ownership(jobs_db, 1) == (ScheduleState.ALIVE.value, 100, 1.0)


def test_reset_batch_compares_a_missing_pid_null_safely(jobs_db):
    """A job read with no pid is reset while it is still unclaimed, but not
    once a controller has claimed it: a missing pid is not a wildcard."""
    _add_job(jobs_db, 1, ScheduleState.LAUNCHING)
    _add_job(jobs_db, 2, ScheduleState.LAUNCHING)
    observed = state.get_jobs_needing_recovery_check()
    _set_ownership(jobs_db,
                   2,
                   schedule_state=ScheduleState.LAUNCHING,
                   controller_pid=100,
                   controller_pid_started_at=1.0)

    assert state.reset_jobs_for_recovery_batch(observed) == [1]

    assert _ownership(jobs_db, 1) == (ScheduleState.WAITING.value, None, None)
    assert _ownership(jobs_db, 2) == (ScheduleState.LAUNCHING.value, 100, 1.0)


def test_batched_writes_handle_more_ids_than_one_chunk(jobs_db):
    job_ids = list(range(1, state._RECOVERY_CHUNK_SIZE * 2 + 5))
    for job_id in job_ids:
        _add_job(jobs_db,
                 job_id,
                 ScheduleState.LAUNCHING,
                 controller_pid=job_id)

    assert state.reset_jobs_for_recovery_batch(
        state.get_jobs_needing_recovery_check()) == job_ids

    states = _schedule_states(jobs_db, job_ids)
    assert all(states[job_id] == (ScheduleState.WAITING.value, None)
               for job_id in job_ids)


def test_batched_writes_empty_input(jobs_db):
    assert state.reset_jobs_for_recovery_batch([]) == []


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
    throttle = mock.Mock()
    monkeypatch.setattr(managed_job_utils, '_throttle_recovery_sweep', throttle)
    yield mock.Mock(engine=jobs_db,
                    throttle=throttle,
                    log_path=tmp_path / 'jobs_recovery.log')


def test_sweep_recovers_every_job_without_a_live_controller(sweep_env):
    """Jobs whose tasks already finished go back to a controller too, which
    does their cleanup and moves them to DONE."""
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
        1: (ScheduleState.WAITING.value, None),
        2: (ScheduleState.WAITING.value, None),
        3: (ScheduleState.WAITING.value, None),
    }


def test_sweep_resets_a_multi_task_job_once(sweep_env, monkeypatch):
    """A multi-task job is one candidate, so it is reset once. Each extra
    reset could undo a claim that landed after the first one."""
    _add_job(sweep_env.engine,
             1,
             ScheduleState.ALIVE,
             task_statuses=(Status.SUCCEEDED, Status.RUNNING, Status.PENDING,
                            Status.PENDING),
             controller_pid=100,
             controller_pid_started_at=1.0)
    monkeypatch.setattr(managed_job_utils, 'controller_process_alive',
                        mock.Mock(return_value=False))
    real_reset = state.reset_jobs_for_recovery_batch
    batches = []

    def _spy(jobs):
        batches.append([job['job_id'] for job in jobs])
        return real_reset(jobs)

    monkeypatch.setattr(managed_job_utils.managed_job_state,
                        'reset_jobs_for_recovery_batch', _spy)

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert batches == [[1]]
    assert _schedule_states(sweep_env.engine, [1]) == {
        1: (ScheduleState.WAITING.value, None)
    }


def test_sweep_keeps_claims_made_while_it_runs(sweep_env, monkeypatch):
    """End-to-end interleaving of a sweep and real controller claims, through
    the production claim path rather than hand-written row updates.

    One job per batch. Right after the sweep resets job_a, a controller claims
    it; then another actor recovers job_b and a controller claims that too,
    before the sweep reaches job_b's batch.

    - job_a keeps its claim: no later batch touches it again.
    - job_b keeps its claim: the sweep's reset loses the compare-and-swap,
      because the pid it read is no longer the job's pid.
    - job_c, untouched by the claims, is still recovered.
    """
    monkeypatch.setattr(managed_job_utils, '_RECOVERY_SWEEP_BATCH_SIZE', 1)
    monkeypatch.setattr(managed_job_utils, 'controller_process_alive',
                        mock.Mock(return_value=False))
    # Candidates are processed in job id order.
    job_a = _add_claimable_job(sweep_env.engine,
                               4,
                               controller_pid=100,
                               controller_pid_started_at=1.0)
    job_b = _add_claimable_job(sweep_env.engine,
                               1,
                               controller_pid=200,
                               controller_pid_started_at=2.0)
    job_c = _add_claimable_job(sweep_env.engine,
                               1,
                               controller_pid=300,
                               controller_pid_started_at=3.0)
    real_reset = state.reset_jobs_for_recovery_batch
    batches = []

    def _reset_then_let_controllers_claim(jobs):
        batches.append([job['job_id'] for job in jobs])
        reset_ids = real_reset(jobs)
        if reset_ids == [job_a]:
            assert _claim_waiting_job(pid=999, pid_started_at=9.0) == job_a
            # Another actor recovers job_b, and a controller claims it.
            assert real_reset([{
                'job_id': job_b,
                'controller_pid': 200,
                'controller_pid_started_at': 2.0,
                'schedule_state': ScheduleState.ALIVE,
            }]) == [job_b]
            assert _claim_waiting_job(pid=888, pid_started_at=8.0) == job_b
        return reset_ids

    monkeypatch.setattr(managed_job_utils.managed_job_state,
                        'reset_jobs_for_recovery_batch',
                        _reset_then_let_controllers_claim)

    managed_job_utils.ha_recovery_for_consolidation_mode()

    assert batches == [[job_a], [job_b], [job_c]]
    launching = ScheduleState.LAUNCHING.value
    assert _ownership(sweep_env.engine, job_a) == (launching, 999, 9.0)
    assert _ownership(sweep_env.engine, job_b) == (launching, 888, 8.0)
    assert _ownership(sweep_env.engine,
                      job_c) == (ScheduleState.WAITING.value, None, None)
    log = sweep_env.log_path.read_text(encoding='utf-8')
    assert f'Reset job(s) [{job_a}] for recovery' in log
    assert f'Skipped recovery of job(s) [{job_b}]' in log
    assert f'Reset job(s) [{job_c}] for recovery' in log
    assert 'Recovered 2 job(s)' in log


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

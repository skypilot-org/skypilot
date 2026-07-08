"""Unit tests for requeueing managed jobs whose controller process died.

Covers:
- state.try_requeue_job_for_recovery: the fenced, budget-charging reset that
  makes a dead-controller job claimable by a fresh controller. Run against a
  real temporary SQLite database.
- The dead-controller branch of utils.update_managed_jobs_statuses: requeue
  vs budget exhaustion vs preserving an already-terminal status.
- state.is_legacy_controller_process: pid-NULL jobs that are managed by the
  scheduler (e.g. requeued and not yet re-claimed) are not legacy, so their
  cancel signals route to the consolidated signal path.
"""
import contextlib
import pathlib
import time
from unittest import mock

import filelock
import pytest
import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import constants as jobs_constants
from sky.jobs import scheduler
from sky.jobs import state
from sky.jobs import utils as managed_job_utils

_PID = 4321
_PID_STARTED_AT = 222.0
_MAX = jobs_constants.EMERGENCY_RECOVERY_MAX_ATTEMPTS
_WINDOW = jobs_constants.EMERGENCY_RECOVERY_RESET_WINDOW_SECONDS


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state.

    Follows the pattern from test_jobs_state.py.
    """
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


def _seed_job(engine,
              job_id: int = 1,
              status: str = 'RUNNING',
              schedule_state: str = 'ALIVE',
              pid=_PID,
              pid_started_at=_PID_STARTED_AT,
              emergency_recovery_count=None,
              last_emergency_recovery_at=None,
              task_statuses=None):
    with engine.connect() as conn:
        conn.execute(state.job_info_table.insert().values(
            spot_job_id=job_id,
            name='test-job',
            schedule_state=schedule_state,
            controller_pid=pid,
            controller_pid_started_at=pid_started_at,
            emergency_recovery_count=emergency_recovery_count,
            last_emergency_recovery_at=last_emergency_recovery_at,
        ))
        for task_id, task_status in enumerate(task_statuses or [status]):
            conn.execute(state.spot_table.insert().values(
                job_name='test-job',
                task_name=f'test-task-{task_id}',
                status=task_status,
                spot_job_id=job_id,
                task_id=task_id,
                last_recovered_at=-1.0,
                job_duration=0.0,
            ))
        conn.commit()


def _job_info_row(engine, job_id: int = 1):
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.select(
                state.job_info_table.c.controller_pid,
                state.job_info_table.c.controller_pid_started_at,
                state.job_info_table.c.schedule_state,
                state.job_info_table.c.emergency_recovery_count,
                state.job_info_table.c.last_emergency_recovery_at).where(
                    state.job_info_table.c.spot_job_id == job_id)).fetchone()


def _task_statuses(engine, job_id: int = 1):
    with engine.connect() as conn:
        rows = conn.execute(
            sqlalchemy.select(state.spot_table.c.status).where(
                state.spot_table.c.spot_job_id == job_id).order_by(
                    state.spot_table.c.task_id)).fetchall()
        return [row[0] for row in rows]


def _job_events(engine, job_id: int = 1):
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.select(
                state.job_events_table.c.new_status,
                state.job_events_table.c.reason).where(
                    state.job_events_table.c.spot_job_id == job_id)).fetchall()


class TestTryRequeueJobForRecovery:
    """state.try_requeue_job_for_recovery: fencing, budget, decay."""

    def test_requeue_first_attempt(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine)
        outcome, attempt = state.try_requeue_job_for_recovery(
            1, _PID, _PID_STARTED_AT)
        assert outcome is state.JobRequeueOutcome.REQUEUED
        assert attempt == 1
        row = _job_info_row(engine)
        assert row.controller_pid is None
        assert row.controller_pid_started_at is None
        assert row.schedule_state == 'WAITING'
        assert row.emergency_recovery_count == 1
        assert row.last_emergency_recovery_at == pytest.approx(time.time(),
                                                               abs=30)
        # Task status is untouched: the fresh controller's resume path
        # dispatches on it.
        assert _task_statuses(engine) == ['RUNNING']

    def test_budget_continues_from_in_place_retries(self,
                                                    _mock_managed_jobs_db_conn):
        # One budget per job: requeues continue the count spent by the
        # controller's in-place emergency retries.
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine,
                  emergency_recovery_count=3,
                  last_emergency_recovery_at=time.time() - 60)
        outcome, attempt = state.try_requeue_job_for_recovery(
            1, _PID, _PID_STARTED_AT)
        assert outcome is state.JobRequeueOutcome.REQUEUED
        assert attempt == 4
        assert _job_info_row(engine).emergency_recovery_count == 4

    def test_decay_starts_a_new_episode(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine,
                  emergency_recovery_count=_MAX,
                  last_emergency_recovery_at=time.time() - _WINDOW - 1)
        outcome, attempt = state.try_requeue_job_for_recovery(
            1, _PID, _PID_STARTED_AT)
        assert outcome is state.JobRequeueOutcome.REQUEUED
        assert attempt == 1

    def test_budget_exhausted_writes_nothing(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine,
                  emergency_recovery_count=_MAX,
                  last_emergency_recovery_at=time.time() - 60)
        outcome, attempt = state.try_requeue_job_for_recovery(
            1, _PID, _PID_STARTED_AT)
        assert outcome is state.JobRequeueOutcome.BUDGET_EXHAUSTED
        assert attempt == _MAX + 1
        row = _job_info_row(engine)
        assert row.controller_pid == _PID
        assert row.schedule_state == 'ALIVE'
        assert row.emergency_recovery_count == _MAX

    @pytest.mark.parametrize('observed_pid,observed_started_at', [
        (_PID + 1, _PID_STARTED_AT),
        (_PID, _PID_STARTED_AT + 1),
        (_PID, None),
    ])
    def test_stale_owner_observation_is_a_noop(self, _mock_managed_jobs_db_conn,
                                               observed_pid,
                                               observed_started_at):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine)
        outcome, _ = state.try_requeue_job_for_recovery(1, observed_pid,
                                                        observed_started_at)
        assert outcome is state.JobRequeueOutcome.LOST_RACE
        row = _job_info_row(engine)
        assert row.controller_pid == _PID
        assert row.schedule_state == 'ALIVE'
        assert row.emergency_recovery_count is None

    def test_null_started_at_matches_null(self, _mock_managed_jobs_db_conn):
        # Pre-#7847 rows have no controller_pid_started_at; observing
        # started_at None must fence against exactly those rows.
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, pid_started_at=None)
        outcome, attempt = state.try_requeue_job_for_recovery(1, _PID, None)
        assert outcome is state.JobRequeueOutcome.REQUEUED
        assert attempt == 1
        assert _job_info_row(engine).schedule_state == 'WAITING'

    @pytest.mark.parametrize('schedule_state', ['DONE', 'WAITING', 'INACTIVE'])
    def test_unclaimable_schedule_states_are_a_noop(self,
                                                    _mock_managed_jobs_db_conn,
                                                    schedule_state):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, schedule_state=schedule_state)
        outcome, _ = state.try_requeue_job_for_recovery(1, _PID,
                                                        _PID_STARTED_AT)
        assert outcome is state.JobRequeueOutcome.LOST_RACE
        assert _job_info_row(engine).schedule_state == schedule_state

    def test_missing_job_is_a_noop(self, _mock_managed_jobs_db_conn):
        outcome, _ = state.try_requeue_job_for_recovery(42, _PID,
                                                        _PID_STARTED_AT)
        assert outcome is state.JobRequeueOutcome.LOST_RACE


class TestIsLegacyControllerProcess:
    """pid-NULL scheduler jobs are not legacy (cancel routing)."""

    def test_unclaimed_scheduler_job_is_not_legacy(self,
                                                   _mock_managed_jobs_db_conn):
        # A requeued (or not-yet-claimed) job has no controller_pid but is
        # managed by the scheduler; cancels must route to the consolidated
        # signal path, not the dead legacy one.
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine,
                  pid=None,
                  pid_started_at=None,
                  schedule_state='WAITING')
        assert not state.is_legacy_controller_process(1)

    def test_pre_scheduler_job_is_legacy(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, pid=None, pid_started_at=None, schedule_state=None)
        assert state.is_legacy_controller_process(1)

    def test_claimed_job_is_not_legacy(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine)
        assert not state.is_legacy_controller_process(1)

    def test_positive_pid_without_started_at_is_legacy(
            self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, pid_started_at=None)
        assert state.is_legacy_controller_process(1)

    def test_negative_pid_is_not_legacy(self, _mock_managed_jobs_db_conn):
        engine = _mock_managed_jobs_db_conn
        _seed_job(engine, pid=-_PID, pid_started_at=None)
        assert not state.is_legacy_controller_process(1)


@pytest.fixture
def _janitor_env(tmp_path, monkeypatch, _mock_managed_jobs_db_conn):
    """Patch the non-DB collaborators of update_managed_jobs_statuses."""
    monkeypatch.setattr(managed_job_utils.constants,
                        'PERSISTENT_RUN_RESTARTING_SIGNAL_FILE',
                        str(tmp_path / 'nonexistent_restart_signal'))
    monkeypatch.setattr(managed_job_utils, 'controller_process_alive',
                        mock.MagicMock(return_value=False))
    monkeypatch.setattr(managed_job_utils.job_lib, 'get_status',
                        mock.MagicMock(return_value=None))
    handle_mock = mock.MagicMock(return_value=mock.MagicMock())
    monkeypatch.setattr(managed_job_utils.global_user_state,
                        'get_handle_from_cluster_name', handle_mock)
    terminate_mock = mock.MagicMock()
    monkeypatch.setattr(managed_job_utils, 'terminate_cluster', terminate_mock)
    start_controllers_mock = mock.MagicMock()
    monkeypatch.setattr(scheduler, 'maybe_start_controllers',
                        start_controllers_mock)
    return {
        'engine': _mock_managed_jobs_db_conn,
        'terminate_cluster': terminate_mock,
        'maybe_start_controllers': start_controllers_mock,
    }


class TestUpdateManagedJobsStatusesRequeue:
    """The janitor's dead-controller branch: requeue/exhaust/preserve."""

    def test_dead_controller_requeues_job(self, _janitor_env):
        engine = _janitor_env['engine']
        _seed_job(engine)
        managed_job_utils.update_managed_jobs_statuses()
        row = _job_info_row(engine)
        assert row.schedule_state == 'WAITING'
        assert row.controller_pid is None
        assert row.emergency_recovery_count == 1
        # Status is preserved for the resume path; the job is not failed and
        # its cluster is not torn down.
        assert _task_statuses(engine) == ['RUNNING']
        _janitor_env['terminate_cluster'].assert_not_called()
        _janitor_env['maybe_start_controllers'].assert_called_once()
        events = _job_events(engine)
        assert len(events) == 1
        assert events[0].new_status == 'RUNNING'
        assert 'requeued' in events[0].reason

    def test_cancelling_job_is_requeued_not_failed(self, _janitor_env):
        # A job cancelled just before its controller died must be requeued so
        # the fresh controller completes the cancellation.
        engine = _janitor_env['engine']
        _seed_job(engine, status='CANCELLING')
        managed_job_utils.update_managed_jobs_statuses()
        assert _job_info_row(engine).schedule_state == 'WAITING'
        assert _task_statuses(engine) == ['CANCELLING']

    def test_budget_exhaustion_fails_the_job(self, _janitor_env):
        engine = _janitor_env['engine']
        _seed_job(engine,
                  emergency_recovery_count=_MAX,
                  last_emergency_recovery_at=time.time() - 60)
        managed_job_utils.update_managed_jobs_statuses()
        assert _task_statuses(engine) == ['FAILED_CONTROLLER']
        assert _job_info_row(engine).schedule_state == 'DONE'
        _janitor_env['terminate_cluster'].assert_called_once()
        _janitor_env['maybe_start_controllers'].assert_not_called()

    def test_terminal_status_is_preserved(self, _janitor_env):
        # Controller died after writing SUCCEEDED but before marking the job
        # DONE: converge the bookkeeping without overriding the status.
        engine = _janitor_env['engine']
        _seed_job(engine, status='SUCCEEDED')
        managed_job_utils.update_managed_jobs_statuses()
        assert _task_statuses(engine) == ['SUCCEEDED']
        assert _job_info_row(engine).schedule_state == 'DONE'
        assert _job_info_row(engine).emergency_recovery_count is None
        _janitor_env['terminate_cluster'].assert_called_once()

    def test_alive_controller_is_untouched(self, _janitor_env, monkeypatch):
        engine = _janitor_env['engine']
        _seed_job(engine)
        monkeypatch.setattr(managed_job_utils, 'controller_process_alive',
                            mock.MagicMock(return_value=True))
        managed_job_utils.update_managed_jobs_statuses()
        row = _job_info_row(engine)
        assert row.schedule_state == 'ALIVE'
        assert row.controller_pid == _PID
        assert _task_statuses(engine) == ['RUNNING']

    def test_multi_task_requeue_events_use_active_task_status(
            self, _janitor_env):
        engine = _janitor_env['engine']
        _seed_job(engine, task_statuses=['SUCCEEDED', 'RUNNING'])
        managed_job_utils.update_managed_jobs_statuses()
        assert _job_info_row(engine).schedule_state == 'WAITING'
        events = _job_events(engine)
        assert len(events) == 1
        assert events[0].new_status == 'RUNNING'


class TestCancelRouting:
    """Cancels of requeued (pid-NULL) jobs use the consolidated path."""

    def test_cancel_of_requeued_job_writes_consolidated_signal(
            self, tmp_path, monkeypatch, _janitor_env):
        engine = _janitor_env['engine']
        # A requeued job: no pid, WAITING, but a non-PENDING status.
        _seed_job(engine,
                  pid=None,
                  pid_started_at=None,
                  schedule_state='WAITING')
        signal_dir = tmp_path / 'signals'
        signal_dir.mkdir()
        monkeypatch.setattr(jobs_constants, 'CONSOLIDATED_SIGNAL_PATH',
                            str(signal_dir))
        legacy_prefix = str(tmp_path / 'legacy_signal_{}')
        monkeypatch.setattr(jobs_constants, 'SIGNAL_FILE_PREFIX', legacy_prefix)

        msg = managed_job_utils.cancel_jobs_by_id([1])

        assert 'scheduled to be cancelled' in msg
        assert (signal_dir / '1').exists()
        assert not pathlib.Path(legacy_prefix.format(1)).exists()

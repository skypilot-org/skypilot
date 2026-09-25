"""Tests for managed job dependencies (``sky jobs launch`` ``depends_on``)."""
import asyncio
import contextlib
from typing import Optional
from unittest import mock

import click
import fastapi
import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy import orm
from sqlalchemy.ext.asyncio import create_async_engine

from sky import exceptions
from sky.client.cli import command
from sky.jobs import state
from sky.jobs.controller import JobController
from sky.jobs.server import core as jobs_core
from sky.jobs.server import server as jobs_server
from sky.jobs.state import ManagedJobScheduleState
from sky.jobs.state import ManagedJobStatus
from sky.server.requests import payloads


@pytest.fixture
def _db(tmp_path, monkeypatch):
    """Isolated SQLite DB for sky.jobs.state (sync + async engines)."""
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        with filelock.FileLock(str(tmp_path / f'.{_section}.lock'), timeout=10):
            yield

    monkeypatch.setattr(state.migration_utils, 'db_lock', _tmp_db_lock)
    monkeypatch.setattr(state._db_manager, '_engine', engine)
    monkeypatch.setattr(state._db_manager, '_engine_async', async_engine)
    state.create_table(engine)
    yield engine


def _add_job(engine,
             job_id: int,
             schedule_state: ManagedJobScheduleState,
             *task_statuses: ManagedJobStatus,
             primary: Optional[list] = None,
             priority: int = 500) -> None:
    with orm.Session(engine) as session:
        session.execute(
            state.sqlalchemy.insert(state.job_info_table).values(
                spot_job_id=job_id,
                name=f'job-{job_id}',
                schedule_state=schedule_state.value,
                priority=priority))
        for task_id, status in enumerate(task_statuses):
            session.execute(
                state.sqlalchemy.insert(state.spot_table).values(
                    spot_job_id=job_id,
                    task_id=task_id,
                    task_name=f'task-{task_id}',
                    status=status.value,
                    is_primary_in_job_group=(None if primary is None else
                                             primary[task_id])))
        session.commit()


def _claim() -> Optional[int]:
    claimed = asyncio.run(state.get_waiting_job_async(pid=1,
                                                      pid_started_at=1.0))
    return None if claimed is None else claimed['job_id']


class TestClaim:

    def test_waits_until_every_dependency_is_done(self, _db):
        _add_job(_db, 1, ManagedJobScheduleState.ALIVE,
                 ManagedJobStatus.RUNNING)
        _add_job(_db, 2, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED)
        _add_job(_db,
                 3,
                 ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING,
                 priority=1000)
        state.set_job_dependencies(3, [1, 2])
        _add_job(_db, 4, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)

        # The blocked job does not hold back a lower-priority one.
        assert _claim() == 4
        assert _claim() is None

        with orm.Session(_db) as session:
            session.execute(
                state.sqlalchemy.update(state.job_info_table).where(
                    state.job_info_table.c.spot_job_id == 1).values(
                        schedule_state=ManagedJobScheduleState.DONE.value))
            session.commit()
        # Claimed once all dependencies are DONE, whatever their outcome.
        assert _claim() == 3

    def test_missing_dependency_does_not_block(self, _db):
        _add_job(_db, 5, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        state.set_job_dependencies(5, [99])
        assert _claim() == 5

    def test_waiting_job_does_not_raise_highest_priority(self, _db):
        _add_job(_db, 1, ManagedJobScheduleState.ALIVE,
                 ManagedJobStatus.RUNNING)
        _add_job(_db,
                 2,
                 ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING,
                 priority=1000)
        state.set_job_dependencies(2, [1])
        _add_job(_db,
                 3,
                 ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING,
                 priority=200)
        assert state.get_managed_jobs_highest_priority() == 200

    def test_unfinished_dependencies(self, _db):
        _add_job(_db, 1, ManagedJobScheduleState.ALIVE,
                 ManagedJobStatus.RUNNING)
        _add_job(_db, 2, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED)
        _add_job(_db, 3, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        _add_job(_db, 4, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        _add_job(_db, 5, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        state.set_job_dependencies(4, [1, 2, 3])
        state.set_job_dependencies(5, [2])
        assert state.get_unfinished_dependencies([4, 5]) == {4: [1, 3]}
        assert state.get_unfinished_dependencies([]) == {}

    def test_dependencies_round_trip(self, _db):
        state.set_job_dependencies(5, [3, 1])
        state.set_job_dependencies(6, [])
        state.set_job_dependencies(7, [5])
        assert state.get_job_dependencies(5) == [1, 3]
        assert state.get_job_dependencies(6) == []
        assert state.get_jobs_dependencies([5, 6, 7]) == {5: [1, 3], 7: [5]}
        assert state.get_jobs_dependencies([]) == {}


class TestOutcome:

    def test_unsucceeded_dependencies(self, _db):
        _add_job(_db, 1, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED, ManagedJobStatus.SUCCEEDED)
        _add_job(_db, 2, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED, ManagedJobStatus.FAILED,
                 ManagedJobStatus.CANCELLED)
        # A job group whose auxiliary task was cancelled after its primary
        # succeeded has succeeded.
        _add_job(_db,
                 3,
                 ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED,
                 ManagedJobStatus.CANCELLED,
                 primary=[True, False])
        _add_job(_db, 4, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.CANCELLED)
        _add_job(_db, 10, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        state.set_job_dependencies(10, [1, 2, 3, 4, 99])

        assert asyncio.run(state.get_unsucceeded_dependencies_async(10)) == [
            (2, ManagedJobStatus.FAILED),
            (4, ManagedJobStatus.CANCELLED),
            (99, None),
        ]
        assert state.get_job_outcome(3) == (True, ManagedJobStatus.SUCCEEDED)
        assert state.get_job_outcome(2) == (False, ManagedJobStatus.FAILED)
        assert state.get_job_outcome(99) == (False, None)

    def test_no_dependencies(self, _db):
        _add_job(_db, 10, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        assert asyncio.run(state.get_unsucceeded_dependencies_async(10)) == []
        assert asyncio.run(state.get_dependencies_finished_at_async(10)) is None

    def test_dependencies_finished_at(self, _db):
        _add_job(_db, 1, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED, ManagedJobStatus.SUCCEEDED)
        _add_job(_db, 2, ManagedJobScheduleState.DONE,
                 ManagedJobStatus.SUCCEEDED)
        _add_job(_db, 10, ManagedJobScheduleState.WAITING,
                 ManagedJobStatus.PENDING)
        state.set_job_dependencies(10, [1, 2])
        with orm.Session(_db) as session:
            for job_id, task_id, end_at in ((1, 0, 100.0), (1, 1, 300.0),
                                            (2, 0, 200.0)):
                session.execute(
                    state.sqlalchemy.update(state.spot_table).where(
                        state.sqlalchemy.and_(
                            state.spot_table.c.spot_job_id == job_id,
                            state.spot_table.c.task_id == task_id)).values(
                                end_at=end_at))
            session.commit()
        assert asyncio.run(
            state.get_dependencies_finished_at_async(10)) == 300.0

    def test_failure_reason_only_on_pending_tasks(self, _db):
        _add_job(_db, 10, ManagedJobScheduleState.ALIVE,
                 ManagedJobStatus.SUCCEEDED, ManagedJobStatus.PENDING)
        asyncio.run(state.set_pending_tasks_failure_reason_async(10, 'why'))
        with orm.Session(_db) as session:
            reasons = session.execute(
                state.sqlalchemy.select(
                    state.spot_table.c.failure_reason).where(
                        state.spot_table.c.spot_job_id == 10).order_by(
                            state.spot_table.c.task_id)).fetchall()
        assert [r[0] for r in reasons] == [None, 'why']


class TestCheckJobDependencies:

    @staticmethod
    def _run(depends_on,
             *,
             consolidation=True,
             rows=None,
             statuses=None,
             outcomes=None,
             active_workspace='default'):
        rows = rows or {}
        statuses = statuses or {}
        outcomes = outcomes or {}

        def _row(job_id):
            workspace = rows.get(job_id)
            if workspace is None:
                return None
            return state.JobInfoRow(job_id=job_id,
                                    name='dep',
                                    workspace=workspace,
                                    user_hash='u',
                                    root_job_id=None,
                                    parent_job_id=None,
                                    parent_task_id=None,
                                    execution='serial')

        with mock.patch.object(jobs_core.managed_job_utils,
                               'is_consolidation_mode',
                               return_value=consolidation), \
             mock.patch.object(jobs_core.managed_job_state,
                               'get_job_info_row', side_effect=_row), \
             mock.patch.object(jobs_core.managed_job_state, 'get_status',
                               side_effect=statuses.get), \
             mock.patch.object(jobs_core.managed_job_state, 'get_job_outcome',
                               side_effect=outcomes.get), \
             mock.patch.object(jobs_core.skypilot_config,
                               'get_active_workspace',
                               return_value=active_workspace):
            return jobs_core._check_job_dependencies(depends_on)

    def test_no_dependencies(self):
        assert self._run(None) == []
        assert self._run([]) == []

    def test_running_and_succeeded_dependencies_deduplicated(self):
        assert self._run([3, 1, 3],
                         rows={
                             1: 'default',
                             3: 'default'
                         },
                         statuses={
                             1: ManagedJobStatus.RUNNING,
                             3: ManagedJobStatus.SUCCEEDED
                         },
                         outcomes={3:
                             (True, ManagedJobStatus.SUCCEEDED)}) == [1, 3]

    def test_requires_consolidation_mode(self):
        with pytest.raises(exceptions.NotSupportedError,
                           match='consolidation mode'):
            self._run([1], consolidation=False)

    def test_missing_dependency(self):
        with pytest.raises(ValueError, match='no such managed job'):
            self._run([1])

    def test_other_workspace(self):
        with pytest.raises(ValueError, match="in workspace 'team-b'"):
            self._run([1],
                      rows={1: 'team-b'},
                      statuses={1: ManagedJobStatus.RUNNING})

    def test_already_ended_without_success(self):
        with pytest.raises(ValueError, match='ended as FAILED'):
            self._run([1],
                      rows={1: 'default'},
                      statuses={1: ManagedJobStatus.FAILED},
                      outcomes={1: (False, ManagedJobStatus.FAILED)})


class TestControllerRun:

    @staticmethod
    def _make_controller():
        controller = JobController.__new__(JobController)
        controller._job_id = 10
        controller._emergency_backoff_seconds = None
        controller._dag = mock.MagicMock()
        controller._dag.is_job_group.return_value = False
        controller._dag.tasks = [mock.MagicMock()]
        controller._run_one_task = mock.AsyncMock(return_value=True)
        controller._cancel_dynamic_members = mock.AsyncMock()
        return controller

    @pytest.mark.asyncio
    @pytest.mark.parametrize(('unsucceeded', 'runs'), [
        ([], True),
        ([(12, ManagedJobStatus.FAILED), (13, None)], False),
    ])
    async def test_runs_only_if_dependencies_succeeded(self, unsucceeded, runs):
        controller = self._make_controller()
        with mock.patch.object(state, 'get_unsucceeded_dependencies_async',
                               new=mock.AsyncMock(return_value=unsucceeded)), \
             mock.patch.object(state, 'get_dependencies_finished_at_async',
                               new=mock.AsyncMock(return_value=300.0)), \
             mock.patch.object(state, 'set_eligible_at_async',
                               new=mock.AsyncMock()) as set_eligible_at, \
             mock.patch.object(state,
                               'set_pending_tasks_failure_reason_async',
                               new=mock.AsyncMock()) as set_reason, \
             mock.patch.object(state, 'set_cancelling_async',
                               new=mock.AsyncMock()), \
             mock.patch.object(state, 'set_cancelled_async',
                               new=mock.AsyncMock()) as set_cancelled, \
             mock.patch('sky.jobs.utils.event_callback_func'):
            await controller.run()

        assert controller._run_one_task.called is runs
        # The tasks that did not run are cancelled either way.
        set_cancelled.assert_called_once()
        if runs:
            set_reason.assert_not_called()
            # The first task became eligible when the last dependency ended.
            set_eligible_at.assert_called_once_with(10, 0, 300.0)
        else:
            set_eligible_at.assert_not_called()
            set_reason.assert_called_once_with(
                10, 'Dependency did not succeed: job 12 (FAILED), '
                'job 13 (not found)')


class TestLaunchRoute:

    @staticmethod
    def _launch(depends_on, consolidation):
        body = payloads.JobsLaunchBody(task='name: t',
                                       name=None,
                                       depends_on=depends_on)
        request = mock.MagicMock()
        with mock.patch.object(jobs_server.managed_jobs_utils,
                               'is_consolidation_mode',
                               return_value=consolidation), \
             mock.patch.object(jobs_server.executor, 'schedule_request_async',
                               new=mock.AsyncMock()) as schedule:
            asyncio.run(jobs_server.launch(request, body))
        return schedule

    def test_rejects_depends_on_without_consolidation_mode(self):
        with pytest.raises(fastapi.HTTPException) as e:
            self._launch([1], consolidation=False)
        assert e.value.status_code == 400

    @pytest.mark.parametrize(('depends_on', 'consolidation'), [
        ([1], True),
        (None, False),
    ])
    def test_schedules_otherwise(self, depends_on, consolidation):
        self._launch(depends_on, consolidation).assert_called_once()


@pytest.mark.parametrize(('value', 'expected'), [
    (None, None),
    ('12', [12]),
    (' 12, 13 ,', [12, 13]),
])
def test_parse_depends_on(value, expected):
    assert command._parse_depends_on(value) == expected


@pytest.mark.parametrize('value', ['', ',', 'abc', '0', '-1', '1.5'])
def test_parse_depends_on_rejects(value):
    with pytest.raises(click.UsageError, match='--depends-on'):
        command._parse_depends_on(value)

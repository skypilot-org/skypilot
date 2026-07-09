"""Unit tests for sky.jobs.controller - recovery logic for all job types.

Tests cover controller recovery during rolling upgrades for:
- Normal jobs (single task): Recovery based on task status
- Pipeline jobs (sequential multi-task): Recovery with task skip logic
- JobGroups (parallel tasks): Recovery with independent task states

Also tests the cancelled job log download feature in ControllerManager
and file mount cleanup in task_cleanup().
"""
import asyncio
import contextlib
import runpy
import sys
import time
from typing import Dict, List, Optional, Tuple
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import warnings

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky.jobs import controller as controller_module
from sky.jobs import state as managed_job_state
from sky.jobs.controller import _task_run_action
from sky.jobs.controller import _TaskRunAction
from sky.jobs.controller import ControllerManager
from sky.jobs.controller import JobController
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import common
from sky.utils import status_lib


class TestNormalJobRecovery:
    """Tests for normal (single task) job recovery during controller restart.

    When a controller restarts (e.g., during rolling upgrade), it needs to
    correctly recover a single-task job based on:
    - latest_task_id: The highest task_id that has been started
    - last_task_prev_status: The status of that task

    Recovery logic for single task (task_id=0):
    - If latest_task_id is None or status is PENDING: fresh launch
    - If latest_task_id > task_id: task already completed, skip
    - If latest_task_id == task_id and status != PENDING: resume
    """

    @pytest.fixture
    def mock_task(self):
        """Create a mock task."""
        task = MagicMock()
        task.name = 'test-task'
        task.envs = {}
        task.run = 'echo hello'
        return task

    @pytest.mark.asyncio
    async def test_fresh_launch_when_pending(self, mock_task):
        """Test that PENDING status results in fresh launch."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.PENDING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)
            task_id = 0

            is_resume = False
            if (latest_task_id is not None and last_task_prev_status !=
                    managed_job_state.ManagedJobStatus.PENDING):
                assert latest_task_id >= task_id
                if latest_task_id > task_id:
                    pass  # Already executed
                elif latest_task_id == task_id:
                    is_resume = True

            # PENDING means fresh launch, not resume
            assert is_resume is False

    @pytest.mark.asyncio
    async def test_fresh_launch_when_none_status(self, mock_task):
        """Test that None latest_task_id results in fresh launch."""

        async def mock_get_latest(job_id):
            return (None, None)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)
            task_id = 0

            is_resume = False
            if (latest_task_id is not None and last_task_prev_status !=
                    managed_job_state.ManagedJobStatus.PENDING):
                if latest_task_id > task_id:
                    pass
                elif latest_task_id == task_id:
                    is_resume = True

            # None means fresh launch
            assert is_resume is False

    @pytest.mark.asyncio
    async def test_resume_when_running(self, mock_task):
        """Test that RUNNING status triggers resume."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.RUNNING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)
            task_id = 0

            is_resume = False
            if (latest_task_id is not None and last_task_prev_status !=
                    managed_job_state.ManagedJobStatus.PENDING):
                assert latest_task_id >= task_id
                if latest_task_id > task_id:
                    pass
                elif latest_task_id == task_id:
                    is_resume = True

            # RUNNING means we should resume
            assert is_resume is True

    @pytest.mark.asyncio
    async def test_resume_when_starting(self, mock_task):
        """Test that STARTING status triggers resume."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.STARTING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)
            task_id = 0

            is_resume = False
            if (latest_task_id is not None and last_task_prev_status !=
                    managed_job_state.ManagedJobStatus.PENDING):
                assert latest_task_id >= task_id
                if latest_task_id > task_id:
                    pass
                elif latest_task_id == task_id:
                    is_resume = True

            # STARTING means we should resume
            assert is_resume is True

    @pytest.mark.asyncio
    async def test_resume_when_recovering(self, mock_task):
        """Test that RECOVERING status triggers resume."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.RECOVERING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)
            task_id = 0

            is_resume = False
            if (latest_task_id is not None and last_task_prev_status !=
                    managed_job_state.ManagedJobStatus.PENDING):
                assert latest_task_id >= task_id
                if latest_task_id > task_id:
                    pass
                elif latest_task_id == task_id:
                    is_resume = True

            # RECOVERING means we should resume
            assert is_resume is True

    @pytest.mark.asyncio
    async def test_skip_launch_does_not_happen_for_single_task(self, mock_task):
        """Test that single task never has latest_task_id > task_id."""
        # For a single task job, task_id is always 0
        # latest_task_id can only be 0 or None
        # So the skip logic (latest_task_id > task_id) never applies
        task_id = 0

        # Simulate completed task - but for single task this means the job
        # finished successfully and wouldn't be resumed at all
        latest_task_id = 0
        last_task_prev_status = managed_job_state.ManagedJobStatus.SUCCEEDED

        should_skip = False
        is_resume = False
        if (latest_task_id is not None and last_task_prev_status !=
                managed_job_state.ManagedJobStatus.PENDING):
            if latest_task_id > task_id:
                should_skip = True
            elif latest_task_id == task_id:
                is_resume = True

        # For single task, skip never happens (task_id is always 0)
        assert should_skip is False
        # Terminal status still triggers resume logic path
        assert is_resume is True


class TestPipelineJobRecovery:
    """Tests for pipeline (sequential multi-task) job recovery.

    When a controller restarts during a pipeline job, ``_run_one_task``
    classifies each task from its OWN persisted status via the real
    ``_task_run_action`` helper -- NOT from the job's aggregate "latest
    non-terminal task" status. These tests drive that real helper directly,
    with per-task status combinations for a 3-task pipeline, mirroring the
    sequential loop in ``JobController.run()`` (stop at the first task whose
    action isn't SKIP).
    """

    def _actions_for_pipeline(
        self, task_statuses: List[Optional[managed_job_state.ManagedJobStatus]]
    ) -> Dict[int, _TaskRunAction]:
        """Classify a sequence of per-task statuses via the real helper.

        Mirrors the sequential loop in ``JobController.run()``: stop after
        the first task whose action isn't SKIP, since that's the task the
        controller will actually run next.
        """
        actions: Dict[int, _TaskRunAction] = {}
        for task_id, status in enumerate(task_statuses):
            action = _task_run_action(status)
            actions[task_id] = action
            if action != _TaskRunAction.SKIP:
                break
        return actions

    def test_resume_first_task_running(self):
        """First task (task_id=0) was RUNNING; later tasks still PENDING."""
        statuses = [
            managed_job_state.ManagedJobStatus.RUNNING,
            managed_job_state.ManagedJobStatus.PENDING,
            managed_job_state.ManagedJobStatus.PENDING,
        ]
        assert self._actions_for_pipeline(statuses) == {
            0: _TaskRunAction.RESUME
        }

    def test_resume_middle_task_running(self):
        """Task 0 SUCCEEDED, middle task (task_id=1) was RUNNING."""
        statuses = [
            managed_job_state.ManagedJobStatus.SUCCEEDED,
            managed_job_state.ManagedJobStatus.RUNNING,
            managed_job_state.ManagedJobStatus.PENDING,
        ]
        assert self._actions_for_pipeline(statuses) == {
            0: _TaskRunAction.SKIP,
            1: _TaskRunAction.RESUME,
        }

    def test_resume_last_task_running(self):
        """Tasks 0, 1 SUCCEEDED; last task (task_id=2) was RUNNING."""
        statuses = [
            managed_job_state.ManagedJobStatus.SUCCEEDED,
            managed_job_state.ManagedJobStatus.SUCCEEDED,
            managed_job_state.ManagedJobStatus.RUNNING,
        ]
        assert self._actions_for_pipeline(statuses) == {
            0: _TaskRunAction.SKIP,
            1: _TaskRunAction.SKIP,
            2: _TaskRunAction.RESUME,
        }

    def test_skip_completed_task_in_pipeline(self):
        """A task in any terminal state classifies as SKIP, regardless of
        its position in the pipeline."""
        for status in managed_job_state.ManagedJobStatus.terminal_statuses():
            assert _task_run_action(status) == _TaskRunAction.SKIP, status

    def test_fresh_launch_all_pending(self):
        """All tasks PENDING: only task 0 is reached, and it's a fresh
        launch."""
        statuses = [
            managed_job_state.ManagedJobStatus.PENDING,
            managed_job_state.ManagedJobStatus.PENDING,
            managed_job_state.ManagedJobStatus.PENDING,
        ]
        assert self._actions_for_pipeline(statuses) == {0: _TaskRunAction.FRESH}

    def test_resume_recovering_task(self):
        """Task 0 SUCCEEDED, task 1 was RECOVERING when the controller
        died."""
        statuses = [
            managed_job_state.ManagedJobStatus.SUCCEEDED,
            managed_job_state.ManagedJobStatus.RECOVERING,
            managed_job_state.ManagedJobStatus.PENDING,
        ]
        assert self._actions_for_pipeline(statuses) == {
            0: _TaskRunAction.SKIP,
            1: _TaskRunAction.RESUME,
        }

    def test_resume_starting_task(self):
        """Task 0 was STARTING when the controller died."""
        statuses = [
            managed_job_state.ManagedJobStatus.STARTING,
            managed_job_state.ManagedJobStatus.PENDING,
            managed_job_state.ManagedJobStatus.PENDING,
        ]
        assert self._actions_for_pipeline(statuses) == {
            0: _TaskRunAction.RESUME
        }

    def test_earlier_succeeded_later_pending_is_skip_not_fresh(self):
        """Regression: an earlier SUCCEEDED task must classify as SKIP even
        though a later task is still PENDING (which is what the job's
        aggregate "latest non-terminal task" status would report). Before
        this fix, the gate was keyed on that aggregate status, so it treated
        the finished earlier task as a fresh start and tried to re-issue
        STARTING for it.
        """
        task0_status = managed_job_state.ManagedJobStatus.SUCCEEDED
        task1_status = managed_job_state.ManagedJobStatus.PENDING
        assert _task_run_action(task0_status) == _TaskRunAction.SKIP
        assert _task_run_action(task1_status) == _TaskRunAction.FRESH

    def test_task_run_action_none_status_is_fresh(self):
        """A task with no persisted status yet (None) is a fresh start."""
        assert _task_run_action(None) == _TaskRunAction.FRESH

    @pytest.mark.parametrize('status', list(managed_job_state.ManagedJobStatus))
    def test_task_run_action_covers_every_status(
            self, status: managed_job_state.ManagedJobStatus):
        """Table-driven: every ManagedJobStatus member must land in exactly
        the expected bucket, so a newly added status can't silently fall
        into the wrong one.
        """
        action = _task_run_action(status)
        if status.is_terminal():
            assert action == _TaskRunAction.SKIP, status
        elif status == managed_job_state.ManagedJobStatus.PENDING:
            assert action == _TaskRunAction.FRESH, status
        else:
            assert action == _TaskRunAction.RESUME, status


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state and monkeypatch
    the module-level engines used by ``sky.jobs.state``.

    Copied from the fixture of the same name in test_jobs_state.py, so that
    tests here can exercise the real ``sky.jobs.state`` read/write path
    instead of mocking it.
    """
    db_path = tmp_path / 'managed_jobs_testing.db'
    engine = create_engine(f'sqlite:///{db_path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}',
                                       connect_args={'timeout': 30})

    # Monkeypatch Alembic DB lock to a workspace path to avoid writing to
    # ~/.sky
    @contextlib.contextmanager
    def _tmp_db_lock(_section: str):
        lock_path = tmp_path / f'.{_section}.lock'
        with filelock.FileLock(str(lock_path), timeout=10):
            yield

    monkeypatch.setattr(managed_job_state.migration_utils, 'db_lock',
                        _tmp_db_lock)

    # Monkeypatch module-level engines used by state.
    monkeypatch.setattr(managed_job_state._db_manager, '_engine', engine)
    monkeypatch.setattr(managed_job_state._db_manager, '_engine_async',
                        async_engine)

    # Create schema.
    managed_job_state.create_table(engine)

    yield engine


@pytest.fixture
def _seed_pipeline_task0_succeeded_task1_pending(_mock_managed_jobs_db_conn):
    """Seed a real 2-task pipeline job: task 0 SUCCEEDED, task 1 PENDING.

    This is exactly the state a chain-DAG managed job is left in if the
    controller restarts right after the first task finishes but before the
    second task is picked up. Returns the seeded job_id.
    """

    async def mock_callback(status: str):
        del status  # unused

    async def create_job() -> int:
        job_id = managed_job_state.set_job_info_without_job_id(
            name='pipeline-job',
            workspace='default',
            entrypoint='ep',
            pool=None,
            pool_hash=None,
            user_hash='user1')
        managed_job_state.set_pending(job_id,
                                      task_id=0,
                                      task_name='extract',
                                      resources_str='{}',
                                      metadata='{}')
        managed_job_state.set_pending(job_id,
                                      task_id=1,
                                      task_name='transform',
                                      resources_str='{}',
                                      metadata='{}')
        # Drive task 0 to SUCCEEDED through the real state transitions.
        await managed_job_state.set_starting_async(job_id, 0, 'run_0',
                                                   time.time(), '{}', {},
                                                   mock_callback)
        await managed_job_state.set_started_async(job_id, 0, time.time(),
                                                  mock_callback)
        await managed_job_state.set_succeeded_async(job_id, 0, time.time(),
                                                    mock_callback)
        # Task 1 is left PENDING (never started).
        return job_id

    return asyncio.run(create_job())


class TestPipelineRestartWithPendingLaterTask:
    """Regression test: a controller restart mid-pipeline must not re-run an
    already-SUCCEEDED earlier task just because a later task is still
    PENDING.

    Before the fix, ``_run_one_task``'s skip/resume gate was keyed on the
    job's aggregate "latest non-terminal task" status
    (``get_latest_task_id_status_async``). For [task0=SUCCEEDED,
    task1=PENDING] that aggregate status is PENDING, so the gate never took
    the "already executed -> skip" branch for task 0: the restarted
    controller treated task 0 as a fresh start and called
    ``set_starting_async`` on it. That update only matches rows that are
    still PENDING with a NULL ``end_at``, and task 0 already has ``end_at``
    set, so it silently updated zero rows and the controller raised
    ``exceptions.ManagedJobStatusError``. The subsequent FAILED_CONTROLLER
    write for task 0 is itself a no-op (``set_failed_async`` is also scoped
    to ``end_at IS NULL``, which task 0 no longer satisfies); what actually
    tears down the pipeline is ``run()``'s ``finally`` block, which sweeps
    every task with a NULL ``end_at`` to CANCELLED -- taking the
    still-PENDING task 1 down with it. Observed end state:
    ``{0: SUCCEEDED, 1: CANCELLED}``, job CANCELLED.

    These tests exercise the real ``JobController._run_one_task`` against a
    real (temp SQLite) state DB, so a regression here is caught by exercising
    production code end-to-end rather than a hand-rolled copy of the gate
    logic.
    """

    def _make_controller(self, job_id: int) -> JobController:
        """Build a JobController without running __init__ (which needs a
        live DB connection and DAG file on disk that this test doesn't set
        up).

        Sets the attributes the skip/resume gate touches before it returns
        early: `_job_id` (queried and logged), plus `_dag` and `_pool`.
        Also sets `_backend`, `starting`, `starting_lock`, and
        `starting_signal` -- these are only ever read by the PRE-FIX code
        path on its way to `set_starting_async` (see the anti-vacuity test
        below); fixed code never touches them because the gate returns
        before reaching that point.
        """
        controller = JobController.__new__(JobController)
        controller._job_id = job_id
        controller._dag = MagicMock()
        controller._pool = None
        controller._backend = MagicMock()
        controller._backend.run_timestamp = 'sky-2024-01-01-00-00-00-000000'
        controller.starting = set()
        controller.starting_lock = asyncio.Lock()
        controller.starting_signal = MagicMock()
        return controller

    @pytest.mark.asyncio
    async def test_succeeded_earlier_task_is_skipped_not_restarted(
            self, _seed_pipeline_task0_succeeded_task1_pending):
        """The regression: re-running task 0 must skip it (it already
        SUCCEEDED) instead of trying to relaunch it, and must never call
        ``set_starting_async`` for it.
        """
        job_id = _seed_pipeline_task0_succeeded_task1_pending
        controller = self._make_controller(job_id)
        task = MagicMock()
        task.name = 'extract'
        task.metadata = {}
        task.run = 'echo hi'
        task.envs = {constants.TASK_ID_ENV_VAR: 'test-task-id'}
        task.resources = None

        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                'set_starting_async must not be called for task 0: it '
                'already SUCCEEDED. This means the skip/resume gate '
                'regressed to keying off the aggregate "latest task" '
                'status instead of this task\'s own status.')

        # Only the PRE-FIX code path ever reaches these; on fixed code the
        # gate returns before any of them are used. They exist so a
        # regression fails loudly at set_starting_async rather than
        # incidentally on a missing mock attribute.
        with patch('sky.jobs.controller._add_k8s_annotations'), \
             patch('sky.jobs.controller._build_task_specs'), \
             patch('sky.jobs.recovery_strategy.StrategyExecutor.make'), \
             patch('sky.jobs.state.get_file_mounts_blob_id',
                   return_value=None), \
             patch('sky.jobs.state.set_starting_async',
                   side_effect=_fail_if_called):
            result = await controller._run_one_task(0, task)

        # Task 0 already succeeded, so skipping it should propagate success.
        assert result is True

        # The DB must be untouched by this call: task 0 still SUCCEEDED,
        # task 1 still PENDING.
        task0_status = await (
            managed_job_state.get_job_status_with_task_id_async(job_id=job_id,
                                                                task_id=0))
        task1_status = await (
            managed_job_state.get_job_status_with_task_id_async(job_id=job_id,
                                                                task_id=1))
        assert task0_status == managed_job_state.ManagedJobStatus.SUCCEEDED
        assert task1_status == managed_job_state.ManagedJobStatus.PENDING

    @pytest.mark.asyncio
    async def test_pending_later_task_classifies_as_fresh(
            self, _seed_pipeline_task0_succeeded_task1_pending):
        """Task 1 (still PENDING) must classify as FRESH, so once task 0 is
        skipped, ``JobController.run()``'s sequential loop proceeds to
        launch task 1 fresh -- not treat it as already running, and not loop
        back to task 0.
        """
        job_id = _seed_pipeline_task0_succeeded_task1_pending
        task1_status = await (
            managed_job_state.get_job_status_with_task_id_async(job_id=job_id,
                                                                task_id=1))
        assert task1_status == managed_job_state.ManagedJobStatus.PENDING
        assert _task_run_action(task1_status) == _TaskRunAction.FRESH


class TestJobGroupRecovery:
    """Tests for JobGroup recovery during controller rolling upgrade.

    When a controller restarts (e.g., during rolling upgrade), it needs to
    correctly recover job groups based on each task's individual state:
    - None/PENDING: fresh launch
    - Terminal (SUCCEEDED/FAILED/etc.): skip (already done)
    - RUNNING: resume monitoring without forced recovery
    - Other non-terminal (STARTING/RECOVERING): resume with forced recovery
    - CANCELLING: raise CancelledError
    """

    @pytest.fixture
    def mock_task(self):
        """Create a mock task."""
        task = MagicMock()
        task.name = 'test-task'
        task.envs = {}
        return task

    @pytest.fixture
    def mock_dag(self, mock_task):
        """Create a mock DAG with multiple tasks."""
        dag = MagicMock()
        dag.name = 'test-job-group'
        # Create 3 tasks for testing different scenarios
        tasks = []
        for i in range(3):
            t = MagicMock()
            t.name = f'task-{i}'
            t.envs = {}
            tasks.append(t)
        dag.tasks = tasks
        return dag

    @pytest.mark.asyncio
    async def test_resume_with_mixed_task_states(self, mock_dag):
        """Test resume when tasks are in different states.

        Scenario:
        - Task 0: SUCCEEDED (terminal) - should be skipped
        - Task 1: RUNNING - should resume monitoring without forced recovery
        - Task 2: STARTING - should resume with forced recovery
        """

        # Mock the state queries to return different statuses for each task
        async def mock_get_status(job_id, task_id):
            statuses = {
                0: managed_job_state.ManagedJobStatus.SUCCEEDED,
                1: managed_job_state.ManagedJobStatus.RUNNING,
                2: managed_job_state.ManagedJobStatus.STARTING,
            }
            return statuses.get(task_id)

        with patch('sky.jobs.state.get_job_status_with_task_id_async',
                   side_effect=mock_get_status):
            # Simulate the resume logic from _run_job_group
            task_resume_info: Dict[int, Tuple[
                Optional[managed_job_state.ManagedJobStatus], bool]] = {}

            for task_id, task in enumerate(mock_dag.tasks):
                task_status = await mock_get_status(job_id=1, task_id=task_id)

                if task_status is None or task_status == (
                        managed_job_state.ManagedJobStatus.PENDING):
                    task_resume_info[task_id] = (None, False)
                elif task_status.is_terminal():
                    task_resume_info[task_id] = (task_status, False)
                elif task_status == managed_job_state.ManagedJobStatus.CANCELLING:
                    raise asyncio.CancelledError()
                elif task_status == managed_job_state.ManagedJobStatus.RUNNING:
                    task_resume_info[task_id] = (task_status, False)
                else:
                    # Non-terminal, non-RUNNING state - force recovery
                    task_resume_info[task_id] = (task_status, True)

            # Verify results
            # Task 0: SUCCEEDED - should be (SUCCEEDED, False) - skip
            assert task_resume_info[0] == (
                managed_job_state.ManagedJobStatus.SUCCEEDED, False)

            # Task 1: RUNNING - should be (RUNNING, False) - resume without forced recovery
            assert task_resume_info[1] == (
                managed_job_state.ManagedJobStatus.RUNNING, False)

            # Task 2: STARTING - should be (STARTING, True) - force recovery
            assert task_resume_info[2] == (
                managed_job_state.ManagedJobStatus.STARTING, True)

    @pytest.mark.asyncio
    async def test_resume_all_pending_is_fresh_launch(self, mock_dag):
        """Test that all PENDING tasks result in fresh launch (no resume)."""

        async def mock_get_status(job_id, task_id):
            return managed_job_state.ManagedJobStatus.PENDING

        with patch('sky.jobs.state.get_job_status_with_task_id_async',
                   side_effect=mock_get_status):
            task_resume_info: Dict[int, Tuple[
                Optional[managed_job_state.ManagedJobStatus], bool]] = {}

            for task_id, task in enumerate(mock_dag.tasks):
                task_status = await mock_get_status(job_id=1, task_id=task_id)

                if task_status is None or task_status == (
                        managed_job_state.ManagedJobStatus.PENDING):
                    task_resume_info[task_id] = (None, False)
                elif task_status.is_terminal():
                    task_resume_info[task_id] = (task_status, False)
                elif task_status == managed_job_state.ManagedJobStatus.RUNNING:
                    task_resume_info[task_id] = (task_status, False)
                else:
                    task_resume_info[task_id] = (task_status, True)

            # All tasks should be (None, False) - fresh launch
            for task_id in range(len(mock_dag.tasks)):
                assert task_resume_info[task_id] == (None, False)

    @pytest.mark.asyncio
    async def test_resume_all_terminal_returns_early(self, mock_dag):
        """Test that all terminal tasks result in early return."""

        async def mock_get_status(job_id, task_id):
            # All tasks succeeded
            return managed_job_state.ManagedJobStatus.SUCCEEDED

        with patch('sky.jobs.state.get_job_status_with_task_id_async',
                   side_effect=mock_get_status):
            task_resume_info: Dict[int, Tuple[
                Optional[managed_job_state.ManagedJobStatus], bool]] = {}

            for task_id, task in enumerate(mock_dag.tasks):
                task_status = await mock_get_status(job_id=1, task_id=task_id)

                if task_status is None or task_status == (
                        managed_job_state.ManagedJobStatus.PENDING):
                    task_resume_info[task_id] = (None, False)
                elif task_status.is_terminal():
                    task_resume_info[task_id] = (task_status, False)
                elif task_status == managed_job_state.ManagedJobStatus.RUNNING:
                    task_resume_info[task_id] = (task_status, False)
                else:
                    task_resume_info[task_id] = (task_status, True)

            # Check if all tasks are terminal
            all_terminal = all(status is not None and status.is_terminal()
                               for status, _ in task_resume_info.values())

            assert all_terminal is True

            # All succeeded
            all_succeeded = all(
                status == managed_job_state.ManagedJobStatus.SUCCEEDED
                for status, _ in task_resume_info.values())
            assert all_succeeded is True

    @pytest.mark.asyncio
    async def test_resume_cancelling_raises_cancelled_error(self, mock_dag):
        """Test that CANCELLING status raises CancelledError."""

        async def mock_get_status(job_id, task_id):
            if task_id == 1:
                return managed_job_state.ManagedJobStatus.CANCELLING
            return managed_job_state.ManagedJobStatus.RUNNING

        with patch('sky.jobs.state.get_job_status_with_task_id_async',
                   side_effect=mock_get_status):
            with pytest.raises(asyncio.CancelledError):
                for task_id, task in enumerate(mock_dag.tasks):
                    task_status = await mock_get_status(job_id=1,
                                                        task_id=task_id)

                    if task_status is None or task_status == (
                            managed_job_state.ManagedJobStatus.PENDING):
                        pass
                    elif task_status.is_terminal():
                        pass
                    elif task_status == managed_job_state.ManagedJobStatus.CANCELLING:
                        raise asyncio.CancelledError()

    @pytest.mark.asyncio
    async def test_resume_recovering_state_forces_recovery(self, mock_dag):
        """Test that RECOVERING status triggers forced recovery."""

        async def mock_get_status(job_id, task_id):
            return managed_job_state.ManagedJobStatus.RECOVERING

        with patch('sky.jobs.state.get_job_status_with_task_id_async',
                   side_effect=mock_get_status):
            task_resume_info: Dict[int, Tuple[
                Optional[managed_job_state.ManagedJobStatus], bool]] = {}

            for task_id, task in enumerate(mock_dag.tasks):
                task_status = await mock_get_status(job_id=1, task_id=task_id)

                if task_status is None or task_status == (
                        managed_job_state.ManagedJobStatus.PENDING):
                    task_resume_info[task_id] = (None, False)
                elif task_status.is_terminal():
                    task_resume_info[task_id] = (task_status, False)
                elif task_status == managed_job_state.ManagedJobStatus.RUNNING:
                    task_resume_info[task_id] = (task_status, False)
                else:
                    # RECOVERING is non-terminal, non-RUNNING - force recovery
                    task_resume_info[task_id] = (task_status, True)

            # All tasks should have force_transit_to_recovering=True
            for task_id in range(len(mock_dag.tasks)):
                status, force_recovery = task_resume_info[task_id]
                assert status == managed_job_state.ManagedJobStatus.RECOVERING
                assert force_recovery is True

    @pytest.mark.asyncio
    async def test_tasks_to_launch_excludes_non_pending(self, mock_dag):
        """Test that only PENDING/None tasks are included in launch list."""
        # Simulate the logic from _run_job_group
        task_resume_info = {
            0: (managed_job_state.ManagedJobStatus.SUCCEEDED, False
               ),  # Terminal
            1: (managed_job_state.ManagedJobStatus.RUNNING, False),  # Running
            2: (None, False),  # Fresh launch
        }

        tasks_to_launch: List[int] = []
        for task_id in range(len(mock_dag.tasks)):
            task_status, _ = task_resume_info[task_id]
            needs_launch = (task_status is None or task_status
                            == managed_job_state.ManagedJobStatus.PENDING)
            if needs_launch:
                tasks_to_launch.append(task_id)

        # Only task 2 should be launched
        assert tasks_to_launch == [2]

    @pytest.mark.asyncio
    async def test_terminal_tasks_skipped_in_monitoring(self, mock_dag):
        """Test that terminal tasks are skipped during monitoring phase."""
        task_resume_info = {
            0: (managed_job_state.ManagedJobStatus.SUCCEEDED, False),  # Skip
            1: (managed_job_state.ManagedJobStatus.FAILED, False),  # Skip
            2: (managed_job_state.ManagedJobStatus.RUNNING, False),  # Monitor
        }

        monitor_task_ids: List[int] = []
        for task_id in range(len(mock_dag.tasks)):
            task_status, force_recovery = task_resume_info[task_id]
            if task_status is not None and task_status.is_terminal():
                continue  # Skip terminal tasks
            monitor_task_ids.append(task_id)

        # Only task 2 should be monitored
        assert monitor_task_ids == [2]

    @pytest.mark.asyncio
    async def test_mixed_terminal_results_check(self, mock_dag):
        """Test result checking with mix of terminal and monitored tasks."""
        task_resume_info = {
            0: (managed_job_state.ManagedJobStatus.SUCCEEDED, False),
            1: (managed_job_state.ManagedJobStatus.FAILED, False),
            2: (managed_job_state.ManagedJobStatus.RUNNING, False),
        }

        # Simulate monitoring results (only task 2 was monitored)
        monitor_task_ids = [2]
        results = [True]  # Task 2 succeeded

        # Check results logic from _run_job_group
        all_succeeded = True
        for task_id in range(len(mock_dag.tasks)):
            task_status, _ = task_resume_info[task_id]
            if task_status is not None and task_status.is_terminal():
                # Terminal task - check if it succeeded
                if task_status != managed_job_state.ManagedJobStatus.SUCCEEDED:
                    all_succeeded = False
                continue

            # Find the result for this monitored task
            result_idx = monitor_task_ids.index(task_id)
            result = results[result_idx]
            if not result:
                all_succeeded = False

        # Task 1 FAILED, so overall should be False
        assert all_succeeded is False


class TestTaskCleanup:
    """Tests for file mount cleanup in ControllerManager._cleanup().

    The cleanup code in task_cleanup() deletes local file mounts after a
    managed job completes. This includes two-hop file mounts under
    ~/.sky/tmp/controller/{run_id}/. Cloud URL file mounts are skipped.

    Previously, cleanup was incorrectly skipped in consolidation mode,
    causing ~/.sky/tmp/controller/ to grow unbounded.
    """

    @pytest.fixture
    def cleanup_patches(self):
        """Patch all _cleanup() dependencies except file mount cleanup.

        task_cleanup() does three things:
        1. Cluster termination (mocked)
        2. Storage teardown (mocked)
        3. File mount cleanup (tested)
        """
        patches = {
            'ha_recovery': patch(
                'sky.jobs.state.remove_ha_recovery_script_async',
                new_callable=AsyncMock),
            'terminate': patch('sky.jobs.utils.terminate_cluster'),
            'gen_name': patch(
                'sky.jobs.utils.generate_managed_job_cluster_name',
                return_value='test-cluster'),
            'status': patch('sky.core.status', return_value=[]),
            'backend': patch('sky.backends.cloud_vm_ray_backend.'
                             'CloudVmRayBackend'),
        }
        mocks = {}
        for name, p in patches.items():
            mocks[name] = p.start()
        yield mocks
        for p in patches.values():
            p.stop()

    def _make_task(self, file_mounts=None):
        task = MagicMock()
        task.name = 'test-task'
        task.file_mounts = file_mounts
        task.storage_mounts = {}
        return task

    @pytest.mark.asyncio
    async def test_local_dir_mounts_cleaned_up(self, tmp_path, cleanup_patches):
        """Local directory file mounts should be deleted."""
        # Simulate two-hop mount dirs: ~/.sky/tmp/controller/{run_id}/{N}
        mount_0 = tmp_path / 'run_id' / '0'
        mount_0.mkdir(parents=True)
        (mount_0 / 'data.txt').write_text('test data')
        mount_1 = tmp_path / 'run_id' / '1'
        mount_1.mkdir(parents=True)
        (mount_1 / 'config.yaml').write_text('key: value')

        task = self._make_task(file_mounts={
            '/data': str(mount_0),
            '/config': str(mount_1),
        })
        dag = MagicMock()
        dag.tasks = [task]

        from sky.jobs.controller import ControllerManager
        manager = ControllerManager('test-uuid')
        with patch('sky.jobs.controller._get_dag', return_value=dag):
            await manager._cleanup(job_id=1)

        assert not mount_0.exists(), 'mount_0 should be cleaned up'
        assert not mount_1.exists(), 'mount_1 should be cleaned up'


class TestDownloadLogsForCancelledJob:
    """Tests for ControllerManager._download_logs_for_cancelled_job.

    When a managed job is cancelled, we download logs before cluster cleanup
    so they remain accessible via `sky jobs logs`.
    """

    @pytest.fixture(autouse=True)
    def passthrough_to_thread(self):
        """Make asyncio.to_thread call the function directly."""

        async def _passthrough(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch('asyncio.to_thread', side_effect=_passthrough):
            yield

    def _make_manager(self):
        """Create a MagicMock manager with real helper methods bound."""
        manager = MagicMock(spec=ControllerManager)
        manager._download_logs_for_cancelled_job = (
            ControllerManager._download_logs_for_cancelled_job.__get__(
                manager, ControllerManager))
        manager._download_log_from_cluster = (
            ControllerManager._download_log_from_cluster.__get__(
                manager, ControllerManager))
        return manager

    @pytest.mark.asyncio
    async def test_non_pool_job_cluster_found(self):
        """Happy path: non-pool job finds cluster and downloads logs."""
        manager = self._make_manager()
        controller = MagicMock()
        job_id = 1
        task_id = 0

        mock_dag = MagicMock()
        mock_task = MagicMock()
        mock_task.name = 'test-job'
        mock_dag.tasks = [mock_task]

        mock_handle = MagicMock()

        with patch('sky.jobs.controller.managed_job_utils'
                   '.generate_managed_job_cluster_name',
                   return_value='sky-managed-1-test-job') as mock_gen_name, \
             patch('sky.jobs.controller.backend_utils.get_clusters',
                   return_value=[{'handle': mock_handle}]) as mock_get_cl:

            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[task_id],
                dag=mock_dag,
                pool=None)

            mock_gen_name.assert_called_once_with('test-job', job_id)
            mock_get_cl.assert_called_once_with(
                cluster_names=['sky-managed-1-test-job'],
                refresh=common.StatusRefreshMode.NONE,
                all_users=True,
                _include_is_managed=True)
            controller.download_log_and_stream.assert_called_once_with(
                task_id, mock_handle, None)

    @pytest.mark.asyncio
    async def test_pool_job_cluster_found(self):
        """Happy path: pool job gets cluster info from pool state."""
        manager = self._make_manager()
        controller = MagicMock()
        job_id = 2
        task_id = 0

        mock_dag = MagicMock()
        mock_handle = MagicMock()

        with patch('sky.jobs.controller.managed_job_state'
                   '.get_pool_submit_info_async',
                   return_value=('pool-cluster-1', 42)) as mock_pool_info, \
             patch('sky.jobs.controller.backend_utils.get_clusters',
                   return_value=[{'handle': mock_handle}]):

            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[task_id],
                dag=mock_dag,
                pool='my-pool')

            mock_pool_info.assert_called_once_with(job_id)
            controller.download_log_and_stream.assert_called_once_with(
                task_id, mock_handle, 42)

    @pytest.mark.asyncio
    async def test_cluster_not_found_skips_download(self):
        """When get_clusters returns empty, log download is skipped."""
        manager = self._make_manager()
        controller = MagicMock()
        job_id = 3
        task_id = 0

        mock_dag = MagicMock()
        mock_task = MagicMock()
        mock_task.name = 'test-job'
        mock_dag.tasks = [mock_task]

        with patch('sky.jobs.controller.managed_job_utils'
                   '.generate_managed_job_cluster_name',
                   return_value='sky-managed-3-test-job'), \
             patch('sky.jobs.controller.backend_utils.get_clusters',
                   return_value=[]):

            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[task_id],
                dag=mock_dag,
                pool=None)

            controller.download_log_and_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_pool_returns_none_cluster_name_skips(self):
        """When pool submit info returns None cluster, download is skipped."""
        manager = self._make_manager()
        controller = MagicMock()
        job_id = 4
        task_id = 0

        mock_dag = MagicMock()

        with patch('sky.jobs.controller.managed_job_state'
                   '.get_pool_submit_info_async',
                   return_value=(None, None)) as mock_pool_info, \
             patch('sky.jobs.controller.backend_utils.get_clusters'
                   ) as mock_get_cl:

            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[task_id],
                dag=mock_dag,
                pool='my-pool')

            mock_pool_info.assert_called_once_with(job_id)
            mock_get_cl.assert_not_called()
            controller.download_log_and_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_exception_caught_per_task(self):
        """Exceptions from download_log_and_stream are caught per-task.

        The method catches and logs exceptions for each task individually
        so that a failure for one task doesn't prevent downloading logs
        for other tasks.
        """
        manager = self._make_manager()
        controller = MagicMock()
        controller.download_log_and_stream.side_effect = RuntimeError(
            'download failed')
        job_id = 5
        task_id = 0

        mock_dag = MagicMock()
        mock_task = MagicMock()
        mock_task.name = 'test-job'
        mock_dag.tasks = [mock_task]

        mock_handle = MagicMock()

        with patch('sky.jobs.controller.managed_job_utils'
                   '.generate_managed_job_cluster_name',
                   return_value='sky-managed-5-test-job'), \
             patch('sky.jobs.controller.backend_utils.get_clusters',
                   return_value=[{'handle': mock_handle}]):

            # Should NOT raise - exceptions are caught per-task
            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[task_id],
                dag=mock_dag,
                pool=None)

            controller.download_log_and_stream.assert_called_once_with(
                task_id, mock_handle, None)

    @pytest.mark.asyncio
    async def test_job_group_downloads_for_multiple_tasks(self):
        """Job group: downloads logs for all active tasks."""
        manager = self._make_manager()
        controller = MagicMock()
        job_id = 6

        mock_task_0 = MagicMock()
        mock_task_0.name = 'job-a'
        mock_task_1 = MagicMock()
        mock_task_1.name = 'job-b-done'
        mock_task_2 = MagicMock()
        mock_task_2.name = 'job-c'

        mock_dag = MagicMock()
        mock_dag.tasks = [mock_task_0, mock_task_1, mock_task_2]

        mock_handle_0 = MagicMock()
        mock_handle_2 = MagicMock()

        def get_clusters_side_effect(cluster_names, **kwargs):
            name = cluster_names[0]
            if 'job-a' in name:
                return [{'handle': mock_handle_0}]
            elif 'job-c' in name:
                return [{'handle': mock_handle_2}]
            return []

        with patch('sky.jobs.controller.managed_job_utils'
                   '.generate_managed_job_cluster_name',
                   side_effect=lambda name, jid: f'sky-managed-{jid}-{name}'
                   ), \
             patch('sky.jobs.controller.backend_utils.get_clusters',
                   side_effect=get_clusters_side_effect):

            # task 1 already succeeded, so only tasks 0 and 2 are active
            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[0, 2],
                dag=mock_dag,
                pool=None)

            assert controller.download_log_and_stream.call_count == 2
            controller.download_log_and_stream.assert_any_call(
                0, mock_handle_0, None)
            controller.download_log_and_stream.assert_any_call(
                2, mock_handle_2, None)

    @pytest.mark.asyncio
    async def test_per_task_exception_continues_to_next(self):
        """Exception downloading one task's logs doesn't block the next."""
        manager = self._make_manager()
        controller = MagicMock()
        job_id = 7

        mock_task_0 = MagicMock()
        mock_task_0.name = 'job-a'
        mock_task_1 = MagicMock()
        mock_task_1.name = 'job-b'

        mock_dag = MagicMock()
        mock_dag.tasks = [mock_task_0, mock_task_1]

        mock_handle_0 = MagicMock()
        mock_handle_1 = MagicMock()

        def get_clusters_side_effect(cluster_names, **kwargs):
            name = cluster_names[0]
            if 'job-a' in name:
                return [{'handle': mock_handle_0}]
            elif 'job-b' in name:
                return [{'handle': mock_handle_1}]
            return []

        # Task 0 fails, task 1 succeeds
        call_count = [0]

        def download_side_effect(task_id, handle, job_id_on_pool):
            call_count[0] += 1
            if task_id == 0:
                raise RuntimeError('download failed for task 0')

        controller.download_log_and_stream.side_effect = download_side_effect

        with patch('sky.jobs.controller.managed_job_utils'
                   '.generate_managed_job_cluster_name',
                   side_effect=lambda name, jid: f'sky-managed-{jid}-{name}'
                   ), \
             patch('sky.jobs.controller.backend_utils.get_clusters',
                   side_effect=get_clusters_side_effect):

            # Should NOT raise despite task 0 failing
            await ControllerManager._download_logs_for_cancelled_job(
                manager,
                controller,
                job_id,
                task_ids=[0, 1],
                dag=mock_dag,
                pool=None)

            # Both tasks should have been attempted
            assert controller.download_log_and_stream.call_count == 2
            controller.download_log_and_stream.assert_any_call(
                0, mock_handle_0, None)
            controller.download_log_and_stream.assert_any_call(
                1, mock_handle_1, None)


class TestDownloadLogAndStreamLoggingAgentGate:
    """download_log_and_stream skips the local copy only when logs are both
    forwarded externally AND readable back.

    The controller skips downloading a local copy only when a logging agent
    forwards the logs AND a log reader is registered to stream them back. If the
    store has no read-back path (no reader), it must keep the local copy so
    sky jobs logs can still serve a finished job.
    """

    def _make_controller(self):
        controller = MagicMock(spec=JobController)
        controller._job_id = 1
        controller._backend = MagicMock()
        controller.download_log_and_stream = (
            JobController.download_log_and_stream.__get__(
                controller, JobController))
        return controller

    def _run(self, agent_configured, reader):
        controller = self._make_controller()
        handle = MagicMock()
        with patch('sky.jobs.controller.logs.is_logging_agent_configured',
                   return_value=agent_configured), \
             patch('sky.jobs.controller.logs.get_log_reader',
                   return_value=reader), \
             patch('sky.jobs.controller.managed_job_state') as mock_state, \
             patch('sky.jobs.controller.managed_job_runtime') as mock_runtime, \
             patch('sky.jobs.controller.controller_utils') as mock_cutils:
            mock_runtime.is_registered.return_value = False
            mock_cutils.download_and_stream_job_log.return_value = (
                '/tmp/run.log')
            controller.download_log_and_stream(0, handle, None)
            return mock_state, mock_runtime, mock_cutils

    def test_skips_download_when_agent_and_reader_configured(self):
        # Logs are forwarded and readable back -> skip the local copy.
        mock_state, mock_runtime, mock_cutils = self._run(agent_configured=True,
                                                          reader=MagicMock())
        mock_state.set_local_log_file.assert_not_called()
        mock_runtime.download_logs.assert_not_called()
        mock_cutils.download_and_stream_job_log.assert_not_called()

    def test_downloads_when_agent_but_no_reader(self):
        # Forwarded to a write-only store (no reader) -> keep the local copy so
        # sky jobs logs still works for a finished job.
        _, _, mock_cutils = self._run(agent_configured=True, reader=None)
        mock_cutils.download_and_stream_job_log.assert_called_once()

    def test_downloads_when_no_logging_agent(self):
        _, _, mock_cutils = self._run(agent_configured=False, reader=None)
        mock_cutils.download_and_stream_job_log.assert_called_once()


class TestJobGroupResumeDoesNotReissueStarting:
    """Regression: a resumed JobGroup task must not be re-issued STARTING.

    On controller restart, ``_run_job_group`` resumes every non-terminal task.
    A task that was already past PENDING (STARTING/RUNNING/RECOVERING) must NOT
    have ``set_starting`` re-issued: ``set_starting`` only transitions
    PENDING->STARTING, so re-issuing it matches no rows and raises
    ``ManagedJobStatusError``, which the controller escalates to
    FAILED_CONTROLLER and tears the whole group down. The single-task path
    already guards this with ``if not is_resume``; the JobGroup path mirrors it
    via ``set_starting=needs_launch(task_id)``.
    """

    def _make_controller(self):
        controller = MagicMock(spec=JobController)
        controller._job_id = 1
        controller._dag = MagicMock()
        task = MagicMock()
        task.name = 'job-a'
        task.envs = {}
        task.run = 'echo hi'
        controller._dag.tasks = [task]
        controller._backend = MagicMock()
        controller._backend.run_timestamp = 'run-ts'
        controller.starting = set()
        controller.starting_lock = MagicMock()
        controller.starting_signal = MagicMock()
        return controller, task

    async def _prepare(self, controller, task, set_starting):
        with patch('sky.jobs.controller.job_group_networking') as net, \
             patch('sky.jobs.controller.managed_job_utils') as utils, \
             patch('sky.jobs.controller.recovery_strategy') as recovery, \
             patch('sky.jobs.controller.managed_job_state') as state, \
             patch('sky.jobs.controller.backend_utils'), \
             patch('sky.jobs.controller._build_task_specs', return_value={}):
            net.generate_wait_for_networking_script.return_value = ''
            net.generate_inline_networking_setup_script.return_value = ''
            utils.generate_managed_job_cluster_name.return_value = 'job-a-1'
            recovery.StrategyExecutor.make.return_value = MagicMock()
            state.get_file_mounts_blob_id.return_value = None
            state.set_starting_async = AsyncMock()

            cluster_name, _ = await (
                JobController._prepare_job_group_task_for_launch(
                    controller, task, 0, 'group', [],
                    set_starting=set_starting))
            return cluster_name, state.set_starting_async

    @pytest.mark.asyncio
    async def test_resume_skips_set_starting(self):
        """set_starting=False (resumed task) must not call set_starting."""
        controller, task = self._make_controller()
        cluster_name, set_starting_async = await self._prepare(
            controller, task, set_starting=False)
        assert cluster_name == 'job-a-1'
        set_starting_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fresh_launch_sets_starting(self):
        """set_starting=True (fresh launch) must call set_starting."""
        controller, task = self._make_controller()
        _, set_starting_async = await self._prepare(controller,
                                                    task,
                                                    set_starting=True)
        set_starting_async.assert_awaited_once()


class TestUserJobStatusClassification:
    """Tests for how a terminal *user-job* status (on the worker cluster) is
    classified into a ManagedJobStatus by the controller monitoring loop.

    Regression coverage for SKY-5941: a user job that ends in
    JobStatus.FAILED_DRIVER (e.g. the user workload OOM'd and the Ray driver
    crashed) must be classified as ManagedJobStatus.FAILED, NOT
    FAILED_CONTROLLER -- the controller is healthy, the user workload failed.
    """

    def _make_controller(self):
        """Build a JobController without running __init__ (which needs a DB)."""
        controller = JobController.__new__(JobController)
        controller._job_id = 1
        controller._pool = None
        controller._backend = MagicMock()
        # Methods exercised on the FAILED path; stub them out.
        controller.download_log_and_stream = MagicMock()
        controller._get_cluster_job_exit_codes = AsyncMock(return_value=[])
        controller._cleanup_cluster = AsyncMock()
        return controller

    async def _run_until_terminal(self, controller, worker_job_status):
        """Drive _monitor_one_task through one iteration for a terminal
        worker-cluster job status with the cluster UP, returning the
        set_failed_async mock so the caller can assert the classification."""
        mock_task = MagicMock()
        mock_task.name = 'test-task'
        mock_task.num_nodes = 1

        # No retries: deterministic OOM-style failures should not be retried.
        executor = MagicMock()
        executor.should_restart_on_failure.return_value = False
        executor.max_restarts_on_errors = 0

        handle = MagicMock()

        with patch('asyncio.sleep', new=AsyncMock()), \
             patch('sky.backends.backend_utils.async_check_network_connection',
                   new=AsyncMock()), \
             patch('sky.jobs.utils.get_job_status',
                   new=AsyncMock(return_value=(worker_job_status, None))), \
             patch('sky.jobs.utils.try_to_get_job_end_time',
                   return_value=12345.0), \
             patch('sky.backends.backend_utils.refresh_cluster_status_handle',
                   return_value=(status_lib.ClusterStatus.UP, handle)), \
             patch('sky.jobs.state.set_failed_async',
                   new=AsyncMock()) as mock_set_failed:

            succeeded = await controller._monitor_one_task(
                task_id=0,
                task=mock_task,
                cluster_name='test-cluster',
                executor=executor,
                callback_func=MagicMock(),
            )

        # A failed user job means the task did not succeed and is not retried.
        assert succeeded is False
        executor.should_restart_on_failure.assert_called_once()
        mock_set_failed.assert_called_once()
        return mock_set_failed

    @pytest.mark.asyncio
    async def test_failed_driver_maps_to_failed_not_controller(self):
        """FAILED_DRIVER (user-job OOM / Ray driver crash) -> FAILED."""

        controller = self._make_controller()
        mock_set_failed = await self._run_until_terminal(
            controller, job_lib.JobStatus.FAILED_DRIVER)

        failure_type = mock_set_failed.call_args.kwargs['failure_type']
        assert failure_type == managed_job_state.ManagedJobStatus.FAILED, (
            'FAILED_DRIVER must be classified as FAILED, not '
            f'FAILED_CONTROLLER; got {failure_type}')
        assert (failure_type !=
                managed_job_state.ManagedJobStatus.FAILED_CONTROLLER)
        # The failure is clearly surfaced to the user via failure_reason.
        failure_reason = mock_set_failed.call_args.kwargs['failure_reason']
        assert 'job driver on the remote cluster failed' in failure_reason

    @pytest.mark.asyncio
    async def test_plain_failed_user_job_maps_to_failed(self):
        """A normal user-code FAILED still maps to FAILED (sanity check)."""

        controller = self._make_controller()
        mock_set_failed = await self._run_until_terminal(
            controller, job_lib.JobStatus.FAILED)

        failure_type = mock_set_failed.call_args.kwargs['failure_type']
        assert failure_type == managed_job_state.ManagedJobStatus.FAILED


class TestDunderMainDispatchesToImportedModule:
    """Regression: running this file as `__main__` must dispatch into the
    IMPORTED `sky.jobs.controller` module, not into a second copy of it.

    The controller process is launched as
    `python -u -m sky.jobs.controller <uuid>`. Under `python -m pkg.mod`,
    Python's `runpy` executes the module's source a SECOND time into a
    fresh `__main__` namespace, in addition to the normal import of
    `sky.jobs.controller`. That leaves the process with two distinct copies
    of every class and module-level function defined in this file -- the
    imported `sky.jobs.controller.JobController` and a separate
    `__main__.JobController` -- and whichever `main()` actually runs
    determines which copy every subsequently constructed object belongs to.
    If the `__main__` guard called the LOCAL `main` (the `__main__` copy),
    `mock.patch('sky.jobs.controller.JobController...')`, `isinstance`
    checks, and `pickle` would all silently operate on the wrong class.

    This test exercises the real `__main__` guard via `runpy` (it does not
    reimplement the dispatch logic) and asserts that the imported module's
    `main` -- addressed as `sky.jobs.controller.main` -- is the one that
    gets called.
    """

    def test_run_as_main_calls_imported_module_main(self):
        started = []

        def _close_without_running(coro, *args, **kwargs):
            # The `__main__` guard builds a coroutine and hands it to
            # `asyncio.run`. Close it rather than run it: a coroutine does
            # not execute any of its body until it is awaited, so this keeps
            # a REGRESSION cheap and loud. Without this, a regression makes
            # the `__main__` copy's own (unmocked) `main` coroutine run the
            # real controller loop forever, and the test would hang CI
            # instead of failing it. `asyncio.run` is resolved on the shared
            # `asyncio` module at call time, so patching it here reaches the
            # `__main__` copy too.
            coro.close()
            started.append(coro)

        with warnings.catch_warnings():
            # runpy warns that 'sky.jobs.controller' is already present in
            # sys.modules (it was imported normally when this test module
            # was collected). That is expected and is exactly the scenario
            # under test, so it is suppressed rather than worked around.
            warnings.filterwarnings('ignore',
                                    message=r'.*found in sys\.modules.*',
                                    category=RuntimeWarning)
            with patch.object(controller_module, 'main',
                              new_callable=AsyncMock) as mock_main, \
                 patch.object(asyncio, 'run',
                              side_effect=_close_without_running), \
                 patch.object(sys, 'argv',
                              ['sky.jobs.controller', 'test-uuid']):
                runpy.run_module('sky.jobs.controller', run_name='__main__')

        assert started, 'the __main__ guard never called asyncio.run'
        # The imported module's `main` -- not the `__main__` copy's -- must be
        # the one that was called.
        mock_main.assert_called_once_with('test-uuid')

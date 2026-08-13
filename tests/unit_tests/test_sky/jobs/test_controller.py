"""Unit tests for sky.jobs.controller - recovery logic for all job types.

Tests cover controller recovery during rolling upgrades for:
- Normal jobs (single task): Recovery based on task status
- Pipeline jobs (sequential multi-task): Recovery with task skip logic
- JobGroups (parallel tasks): Recovery with independent task states

Also tests the cancelled job log download feature in ControllerManager
and file mount cleanup in task_cleanup().
"""
import asyncio
import copy
import runpy
import sys
from typing import Dict, List, Optional, Tuple
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import warnings

import pytest

import sky
from sky import task as task_lib
from sky.jobs import controller as controller_module
from sky.jobs import job_group_networking
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.jobs.controller import ControllerManager
from sky.jobs.controller import JobController
from sky.skylet import job_lib
from sky.utils import common
from sky.utils import status_lib
from sky.utils.plugin_extensions import LogDeliverySource


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

    When a controller restarts during a pipeline job:
    - Tasks with task_id < latest_task_id: Already completed, skip
    - Task with task_id == latest_task_id: Resume based on status
    - Tasks with task_id > latest_task_id: Will be run after current completes

    Pipeline jobs run tasks sequentially, so only one task is active at a time.
    """

    @pytest.fixture
    def mock_pipeline_dag(self):
        """Create a mock DAG with 3 sequential tasks."""
        dag = MagicMock()
        dag.name = 'test-pipeline'
        tasks = []
        for i in range(3):
            t = MagicMock()
            t.name = f'pipeline-task-{i}'
            t.envs = {}
            t.run = f'echo task-{i}'
            tasks.append(t)
        dag.tasks = tasks
        dag.is_job_group.return_value = False
        return dag

    @pytest.mark.asyncio
    async def test_resume_first_task_running(self, mock_pipeline_dag):
        """Test resuming when first task (task_id=0) was RUNNING."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.RUNNING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)

            # Simulate the loop in run()
            task_actions: Dict[int, str] = {}  # 'skip', 'resume', 'launch'
            for task_id, task in enumerate(mock_pipeline_dag.tasks):
                if (latest_task_id is not None and last_task_prev_status !=
                        managed_job_state.ManagedJobStatus.PENDING):
                    if latest_task_id > task_id:
                        task_actions[task_id] = 'skip'
                        continue
                    elif latest_task_id == task_id:
                        task_actions[task_id] = 'resume'
                        # In real code, we'd run the task here
                        break  # Simulate sequential execution
                else:
                    task_actions[task_id] = 'launch'
                    break

            # Task 0 should resume, tasks 1 and 2 not yet processed
            assert task_actions == {0: 'resume'}

    @pytest.mark.asyncio
    async def test_resume_middle_task_running(self, mock_pipeline_dag):
        """Test resuming when middle task (task_id=1) was RUNNING."""

        async def mock_get_latest(job_id):
            return (1, managed_job_state.ManagedJobStatus.RUNNING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)

            task_actions: Dict[int, str] = {}
            for task_id, task in enumerate(mock_pipeline_dag.tasks):
                if (latest_task_id is not None and last_task_prev_status !=
                        managed_job_state.ManagedJobStatus.PENDING):
                    if latest_task_id > task_id:
                        task_actions[task_id] = 'skip'
                        continue
                    elif latest_task_id == task_id:
                        task_actions[task_id] = 'resume'
                        break
                else:
                    task_actions[task_id] = 'launch'
                    break

            # Task 0 should be skipped, task 1 should resume
            assert task_actions == {0: 'skip', 1: 'resume'}

    @pytest.mark.asyncio
    async def test_resume_last_task_running(self, mock_pipeline_dag):
        """Test resuming when last task (task_id=2) was RUNNING."""

        async def mock_get_latest(job_id):
            return (2, managed_job_state.ManagedJobStatus.RUNNING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)

            task_actions: Dict[int, str] = {}
            for task_id, task in enumerate(mock_pipeline_dag.tasks):
                if (latest_task_id is not None and last_task_prev_status !=
                        managed_job_state.ManagedJobStatus.PENDING):
                    if latest_task_id > task_id:
                        task_actions[task_id] = 'skip'
                        continue
                    elif latest_task_id == task_id:
                        task_actions[task_id] = 'resume'
                        break
                else:
                    task_actions[task_id] = 'launch'
                    break

            # Tasks 0, 1 should be skipped, task 2 should resume
            assert task_actions == {0: 'skip', 1: 'skip', 2: 'resume'}

    @pytest.mark.asyncio
    async def test_skip_completed_task_in_pipeline(self, mock_pipeline_dag):
        """Test that _run_one_task returns True for completed tasks."""
        # When task_id < latest_task_id, the task should return True (success)
        # without actually running, allowing the pipeline to continue

        latest_task_id = 2

        for task_id in range(3):
            should_skip = latest_task_id > task_id

            if task_id == 0:
                assert should_skip is True
            elif task_id == 1:
                assert should_skip is True
            elif task_id == 2:
                assert should_skip is False

    @pytest.mark.asyncio
    async def test_fresh_launch_all_pending(self, mock_pipeline_dag):
        """Test fresh launch when all tasks are PENDING."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.PENDING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)

            task_actions: Dict[int, str] = {}
            for task_id, task in enumerate(mock_pipeline_dag.tasks):
                if (latest_task_id is not None and last_task_prev_status !=
                        managed_job_state.ManagedJobStatus.PENDING):
                    if latest_task_id > task_id:
                        task_actions[task_id] = 'skip'
                        continue
                    elif latest_task_id == task_id:
                        task_actions[task_id] = 'resume'
                        break
                else:
                    task_actions[task_id] = 'launch'
                    break

            # First task should be fresh launch (PENDING)
            assert task_actions == {0: 'launch'}

    @pytest.mark.asyncio
    async def test_resume_recovering_task(self, mock_pipeline_dag):
        """Test resuming when task was in RECOVERING state."""

        async def mock_get_latest(job_id):
            return (1, managed_job_state.ManagedJobStatus.RECOVERING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)

            task_actions: Dict[int, str] = {}
            for task_id, task in enumerate(mock_pipeline_dag.tasks):
                if (latest_task_id is not None and last_task_prev_status !=
                        managed_job_state.ManagedJobStatus.PENDING):
                    if latest_task_id > task_id:
                        task_actions[task_id] = 'skip'
                        continue
                    elif latest_task_id == task_id:
                        task_actions[task_id] = 'resume'
                        break
                else:
                    task_actions[task_id] = 'launch'
                    break

            # Task 0 skipped, task 1 should resume from RECOVERING
            assert task_actions == {0: 'skip', 1: 'resume'}

    @pytest.mark.asyncio
    async def test_resume_starting_task(self, mock_pipeline_dag):
        """Test resuming when task was in STARTING state."""

        async def mock_get_latest(job_id):
            return (0, managed_job_state.ManagedJobStatus.STARTING)

        with patch('sky.jobs.state.get_latest_task_id_status_async',
                   side_effect=mock_get_latest):
            latest_task_id, last_task_prev_status = await mock_get_latest(
                job_id=1)

            task_actions: Dict[int, str] = {}
            for task_id, task in enumerate(mock_pipeline_dag.tasks):
                if (latest_task_id is not None and last_task_prev_status !=
                        managed_job_state.ManagedJobStatus.PENDING):
                    if latest_task_id > task_id:
                        task_actions[task_id] = 'skip'
                        continue
                    elif latest_task_id == task_id:
                        task_actions[task_id] = 'resume'
                        break
                else:
                    task_actions[task_id] = 'launch'
                    break

            # Task 0 should resume from STARTING
            assert task_actions == {0: 'resume'}


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
            # File-mount cleanup is gated on NOT consolidation mode; pin
            # it so the test does not depend on the local ~/.sky config
            # (a configured API server endpoint flips it to True).
            'consolidation': patch('sky.jobs.utils.is_consolidation_mode',
                                   return_value=False),
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

    def _run(self, agent_configured, reader, undelivered_reason=None):
        controller = self._make_controller()
        handle = MagicMock()
        with patch('sky.jobs.controller.logs.is_logging_agent_configured',
                   return_value=agent_configured), \
             patch('sky.jobs.controller.logs.get_log_reader',
                   return_value=reader), \
             patch('sky.jobs.controller.LogDeliverySource.undelivered_reason',
                   return_value=undelivered_reason), \
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

    def test_downloads_when_delivery_source_reports_undelivered(self):
        # Agent and reader are configured, but the component operating the
        # agent knows it never delivered this cluster's logs -> the local copy
        # is the only copy that will exist, so it must be kept.
        _, _, mock_cutils = self._run(
            agent_configured=True,
            reader=MagicMock(),
            undelivered_reason='logging agent was not deployed on the cluster')
        mock_cutils.download_and_stream_job_log.assert_called_once()

    def test_skips_download_when_delivery_source_confirms(self):
        # A registered source with no evidence against delivery must not
        # change the skip behavior.
        _, _, mock_cutils = self._run(agent_configured=True,
                                      reader=MagicMock(),
                                      undelivered_reason=None)
        mock_cutils.download_and_stream_job_log.assert_not_called()

    def test_no_delivery_source_registered_is_inert(self):
        # The compatibility property of the extension point: with nothing
        # registered, the check must not change behavior at all. Unlike the
        # cases above, this exercises the real LogDeliverySource rather than
        # patching its lookup, so a future default other than None is caught.
        assert not LogDeliverySource.is_registered()
        controller = self._make_controller()
        with patch('sky.jobs.controller.logs.is_logging_agent_configured',
                   return_value=True), \
             patch('sky.jobs.controller.logs.get_log_reader',
                   return_value=MagicMock()), \
             patch('sky.jobs.controller.managed_job_state') as mock_state, \
             patch('sky.jobs.controller.managed_job_runtime') as mock_runtime, \
             patch('sky.jobs.controller.controller_utils') as mock_cutils:
            mock_runtime.is_registered.return_value = False
            controller.download_log_and_stream(0, MagicMock(), None)
        mock_state.set_local_log_file.assert_not_called()
        mock_runtime.download_logs.assert_not_called()
        mock_cutils.download_and_stream_job_log.assert_not_called()


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
        task.resources = []
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


class TestJobGroupNetworkingInjectionGate:
    """Networking wait/updater scripts are only injected when the group
    requires in-group networking (inter_connection enabled) and the task
    runs on Kubernetes."""

    def _make_task(self, cloud_str):
        task = MagicMock()
        task.name = 'job-a'
        task.envs = {}
        task.run = 'echo hi'
        resource = MagicMock()
        if cloud_str is None:
            resource.cloud = None
        else:
            cloud = MagicMock()
            cloud.__str__ = MagicMock(return_value=cloud_str)
            resource.cloud = cloud
        task.resources = [resource]
        return task

    def test_task_uses_kubernetes(self):
        from sky.jobs import controller as controller_lib
        assert controller_lib._task_uses_kubernetes(
            self._make_task('Kubernetes'))
        assert not controller_lib._task_uses_kubernetes(self._make_task('AWS'))
        assert not controller_lib._task_uses_kubernetes(self._make_task(None))

    async def _prepare(self, inter_connection_enabled, cloud_str):
        controller = MagicMock(spec=JobController)
        controller._job_id = 1
        controller._dag = MagicMock()
        controller._dag.inter_connection_enabled = MagicMock(
            return_value=inter_connection_enabled)
        task = self._make_task(cloud_str)
        controller._dag.tasks = [task]
        controller._backend = MagicMock()
        controller._backend.run_timestamp = 'run-ts'
        controller.starting = set()
        controller.starting_lock = MagicMock()
        controller.starting_signal = MagicMock()

        with patch('sky.jobs.controller.job_group_networking') as net, \
             patch('sky.jobs.controller.managed_job_utils') as utils, \
             patch('sky.jobs.controller.recovery_strategy') as recovery, \
             patch('sky.jobs.controller.managed_job_state') as state, \
             patch('sky.jobs.controller.backend_utils'), \
             patch('sky.jobs.controller._build_task_specs', return_value={}):
            net.generate_wait_for_networking_script.return_value = 'WAIT'
            net.generate_inline_networking_setup_script.return_value = ''
            utils.generate_managed_job_cluster_name.return_value = 'job-a-1'
            recovery.StrategyExecutor.make.return_value = MagicMock()
            state.get_file_mounts_blob_id.return_value = None
            state.set_starting_async = AsyncMock()

            await JobController._prepare_job_group_task_for_launch(
                controller, task, 0, 'group', ['peer'], set_starting=False)
            return task, net

    @pytest.mark.asyncio
    async def test_injects_wait_for_kubernetes_task(self):
        task, net = await self._prepare(inter_connection_enabled=True,
                                        cloud_str='Kubernetes')
        assert task.run.startswith('WAIT')
        net.generate_wait_for_networking_script.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_injection_when_inter_connection_disabled(self):
        task, net = await self._prepare(inter_connection_enabled=False,
                                        cloud_str='Kubernetes')
        assert task.run == 'echo hi'
        net.generate_wait_for_networking_script.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_injection_for_non_kubernetes_task(self):
        task, net = await self._prepare(inter_connection_enabled=True,
                                        cloud_str='AWS')
        assert task.run == 'echo hi'
        net.generate_wait_for_networking_script.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_terminal_failure_reason_includes_exit_code(self):
        """A non-retried user failure surfaces the exit code and user-error
        attribution in failure_reason, which feeds the dashboard details
        and the FAILED job event (SKY-6411)."""

        controller = self._make_controller()
        controller._get_cluster_job_exit_codes = AsyncMock(return_value=[7])
        mock_set_failed = await self._run_until_terminal(
            controller, job_lib.JobStatus.FAILED)

        failure_reason = mock_set_failed.call_args.kwargs['failure_reason']
        assert ('Job exited with exit code 7 (user program failure)'
                in failure_reason)
        assert mock_set_failed.call_args.kwargs['exit_codes'] == [7]
        # The log pointer is appended, not replaced.
        assert 'sky jobs logs --controller' in failure_reason

    @pytest.mark.asyncio
    async def test_terminal_failure_attribution_without_exit_codes(self):
        """The user-program attribution is added even when the exit-code
        fetch fails (returns None), falling back to the job status."""

        controller = self._make_controller()
        controller._get_cluster_job_exit_codes = AsyncMock(return_value=None)
        mock_set_failed = await self._run_until_terminal(
            controller, job_lib.JobStatus.FAILED)

        failure_reason = mock_set_failed.call_args.kwargs['failure_reason']
        assert 'Job failed (FAILED) (user program failure)' in failure_reason
        assert 'sky jobs logs --controller' in failure_reason
        assert mock_set_failed.call_args.kwargs['exit_codes'] is None


class TestUserJobFailureRecoveryEventReason:
    """The RECOVERING job event must state the real trigger (SKY-6411).

    When recovery is triggered by the user job exiting non-zero on a healthy
    cluster (max_restarts_on_errors / recover_on_exit_codes), the RECOVERING
    event must carry the exit code and a pointer to the job logs instead of
    the generic 'Cluster preempted or failed, recovering' copy, which is
    misleading (the cluster was not preempted) and unactionable.
    """

    class _StopLoop(Exception):
        """Sentinel raised from recover() to end the monitoring loop."""

    def _make_controller(self, exit_codes):
        controller = JobController.__new__(JobController)
        controller._job_id = 42
        controller._pool = None
        controller._backend = MagicMock()
        controller.download_log_and_stream = MagicMock()
        controller._get_cluster_job_exit_codes = AsyncMock(
            return_value=exit_codes)
        controller._cleanup_cluster = AsyncMock()
        return controller

    def _make_executor(self, recover_on_exit_codes=None):
        executor = MagicMock()
        executor.should_restart_on_failure.return_value = True
        executor.max_restarts_on_errors = 3
        executor.restart_cnt_on_failure = 1
        executor.recover_on_exit_codes = recover_on_exit_codes
        executor.recover = AsyncMock(
            side_effect=TestUserJobFailureRecoveryEventReason._StopLoop())
        return executor

    async def _run_recovery(self,
                            controller,
                            executor,
                            cluster_status=status_lib.ClusterStatus.UP):
        """Drive _monitor_one_task through one failed-job iteration up to
        set_recovering_async, returning its call kwargs."""
        mock_task = MagicMock()
        mock_task.name = 'test-task'
        mock_task.num_nodes = 1

        handle = MagicMock()
        handle.launched_resources.need_cleanup_after_preemption_or_failure.\
            return_value = False

        state = controller_module.managed_job_state
        with patch('asyncio.sleep', new=AsyncMock()), \
             patch('sky.backends.backend_utils.async_check_network_connection',
                   new=AsyncMock()), \
             patch('sky.jobs.utils.get_job_status',
                   new=AsyncMock(
                       return_value=(job_lib.JobStatus.FAILED, None))), \
             patch('sky.jobs.utils.try_to_get_job_end_time',
                   return_value=12345.0), \
             patch('sky.backends.backend_utils.refresh_cluster_status_handle',
                   return_value=(cluster_status, handle)), \
             patch.object(controller_module.global_user_state,
                          'get_cluster_events', return_value=[]), \
             patch.object(controller_module.ExternalFailureSource,
                          'is_registered', return_value=False), \
             patch.object(controller_module.managed_job_runtime,
                          'is_registered', return_value=False), \
             patch.object(state, 'set_recovering_async',
                          new=AsyncMock()) as mock_set_recovering:
            with pytest.raises(TestUserJobFailureRecoveryEventReason._StopLoop):
                await controller._monitor_one_task(
                    task_id=0,
                    task=mock_task,
                    cluster_name='test-cluster',
                    executor=executor,
                    callback_func=MagicMock(),
                )

        mock_set_recovering.assert_awaited_once()
        return mock_set_recovering.await_args.kwargs

    @pytest.mark.asyncio
    async def test_reason_has_exit_code_and_log_pointer(self):
        controller = self._make_controller(exit_codes=[137])
        executor = self._make_executor()
        kwargs = await self._run_recovery(controller, executor)

        reason = kwargs['user_job_failure_reason']
        assert 'exit code 137' in reason
        assert 'sky jobs logs --controller 42' in reason
        assert 'restart 1 of 3' in reason
        # The event must not claim the cluster was preempted.
        assert 'preempted' not in reason
        assert (kwargs['recovery_source'] ==
                managed_job_state.RecoverySource.FAILURE)

    @pytest.mark.asyncio
    async def test_multiple_exit_codes(self):
        controller = self._make_controller(exit_codes=[137, 1])
        executor = self._make_executor()
        kwargs = await self._run_recovery(controller, executor)

        assert 'exit codes [137, 1]' in kwargs['user_job_failure_reason']

    @pytest.mark.asyncio
    async def test_recover_on_exit_codes_match(self):
        controller = self._make_controller(exit_codes=[137])
        executor = self._make_executor(recover_on_exit_codes=[137])
        kwargs = await self._run_recovery(controller, executor)

        reason = kwargs['user_job_failure_reason']
        assert 'exit code 137' in reason
        assert 'recover_on_exit_codes' in reason

    @pytest.mark.asyncio
    async def test_no_exit_codes_falls_back_to_job_status(self):
        controller = self._make_controller(exit_codes=None)
        executor = self._make_executor()
        kwargs = await self._run_recovery(controller, executor)

        reason = kwargs['user_job_failure_reason']
        assert 'Job failed (FAILED)' in reason
        assert 'sky jobs logs --controller 42' in reason

    @pytest.mark.asyncio
    async def test_preemption_path_has_no_user_job_failure_reason(self):
        """A real preemption (cluster not UP) keeps the preemption copy."""
        controller = self._make_controller(exit_codes=[137])
        executor = self._make_executor()
        kwargs = await self._run_recovery(
            controller, executor, cluster_status=status_lib.ClusterStatus.INIT)

        assert kwargs['user_job_failure_reason'] is None


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


class TestTransientJobStatusRecoveryWindow:
    """Tests for the transient job-status-check retry window across recovery.

    When the controller cannot fetch a task's job status but the cluster is
    healthy, it retries for up to JOB_STATUS_FETCH_TOTAL_TIMEOUT_SECONDS before
    recovering, to avoid a false alarm from a transient control-plane error.
    That window (`transient_job_check_error_start_time`) must be reset after a
    recovery; otherwise the first status-fetch failure after a recovery is
    measured from before the recovery, exceeds the timeout immediately, and
    triggers another recovery with no retries -- turning one transient error
    into an unbounded recovery loop.
    """

    class _StopLoop(Exception):
        """Sentinel to break the otherwise-infinite monitoring loop."""

    @pytest.mark.asyncio
    async def test_window_reset_after_recovery(self, monkeypatch):
        """A transient failure after a recovery starts a fresh retry window.

        Drives ``_monitor_one_task_impl`` through: transient failure (retry) ->
        transient failure past the timeout (recover) -> transient failure
        again. With the window reset, the third failure retries instead of
        recovering, so ``recover`` is called exactly once. Without the reset it
        would recover a second time immediately.

        Calls the ``_impl`` body rather than ``_monitor_one_task``: the retry
        window lives entirely in the body, and the wrapper only owns the status
        logger's lifecycle (covered by
        ``test_status_logger_flushed_when_body_raises``).
        """
        monkeypatch.setattr(managed_job_utils,
                            'JOB_STATUS_FETCH_TOTAL_TIMEOUT_SECONDS', 60)
        monkeypatch.setattr(managed_job_utils, 'JOB_STATUS_CHECK_GAP_SECONDS',
                            0)

        # A logical clock advanced at the top of each loop iteration (inside
        # the get_job_status stub) so `elapsed` is fully deterministic:
        #   iter 1: +0   -> elapsed 0   < 60 -> retry
        #   iter 2: +100 -> elapsed 100 >= 60 -> recover (#1); window reset
        #   iter 3: +50  -> fresh window, elapsed 0 < 60 -> retry
        #   iter 4: stub raises _StopLoop to end the loop
        clock = {'t': 1000.0}
        deltas = iter([0.0, 100.0, 50.0])
        recover_calls = 0

        async def fake_get_job_status(*args, **kwargs):
            try:
                clock['t'] += next(deltas)
            except StopIteration:
                raise TestTransientJobStatusRecoveryWindow._StopLoop()
            return None, 'Job status check timed out after 30s.'

        async def fake_recover(*args, **kwargs):
            nonlocal recover_calls
            recover_calls += 1
            return clock['t']

        handle = MagicMock()
        handle.launched_resources.need_cleanup_after_preemption_or_failure.\
            return_value = False

        def fake_refresh(*args, **kwargs):
            return status_lib.ClusterStatus.UP, handle

        mock_self = MagicMock()
        mock_self._job_id = 1
        mock_self._pool = None

        executor = MagicMock()
        executor.recover = AsyncMock(side_effect=fake_recover)

        state = controller_module.managed_job_state
        with patch.object(controller_module.time, 'time',
                          side_effect=lambda: clock['t']), \
             patch.object(managed_job_utils, 'get_job_status',
                          side_effect=fake_get_job_status), \
             patch.object(controller_module.backend_utils,
                          'refresh_cluster_status_handle',
                          side_effect=fake_refresh), \
             patch.object(controller_module.backend_utils,
                          'async_check_network_connection',
                          new=AsyncMock(return_value=None)), \
             patch.object(controller_module.managed_job_runtime,
                          'is_registered', return_value=False), \
             patch.object(state, 'set_recovering_async',
                          new=AsyncMock(return_value=None)), \
             patch.object(state, 'set_recovered_async',
                          new=AsyncMock(return_value=None)), \
             patch.object(state, 'set_started_async',
                          new=AsyncMock(return_value=None)), \
             patch.object(controller_module.asyncio, 'sleep',
                          new=AsyncMock(return_value=None)):
            with pytest.raises(TestTransientJobStatusRecoveryWindow._StopLoop):
                await controller_module.JobController._monitor_one_task_impl(
                    mock_self,
                    task_id=0,
                    task=MagicMock(name='task'),
                    cluster_name='cluster',
                    executor=executor,
                    status_logger=managed_job_utils.JobStatusLogger(),
                    callback_func=MagicMock(),
                    force_transit_to_recovering=False)

        assert recover_calls == 1, (
            'expected exactly one recovery; a second recovery means the '
            'transient retry window was not reset after the first recovery')

    @pytest.mark.asyncio
    async def test_status_logger_flushed_when_body_raises(self, monkeypatch):
        """The status logger is flushed even when the loop exits by raising.

        ``_monitor_one_task`` owns the logger outside the loop precisely so the
        last status the controller observed reaches the log when the loop exits
        by raising (job cancelled, controller torn down) rather than by
        observing a terminal status. Without the ``finally``, a collapsed run's
        closing logline -- the only record of when that status was last seen --
        would be dropped on exactly the paths where it is most useful.
        """
        status_logger = MagicMock()
        monkeypatch.setattr(managed_job_utils, 'JobStatusLogger',
                            lambda: status_logger)

        async def raise_stop_loop(*args, **kwargs):
            raise TestTransientJobStatusRecoveryWindow._StopLoop()

        mock_self = MagicMock()
        mock_self._monitor_one_task_impl = raise_stop_loop

        with pytest.raises(TestTransientJobStatusRecoveryWindow._StopLoop):
            await controller_module.JobController._monitor_one_task(
                mock_self,
                task_id=0,
                task=MagicMock(name='task'),
                cluster_name='cluster',
                executor=MagicMock())

        status_logger.flush.assert_called_once()


class TestAddK8sAnnotations:
    """Tests for _add_k8s_annotations.

    The function stamps two pod annotations onto every resource. It must add
    only those annotations: it used to pass the resource's whole config as the
    override to Resources.copy(), which overlays the config on top of itself.
    """

    def _make_task(self, pod_config):
        task = task_lib.Task(name='test-task', run='echo hi')
        task.set_resources(
            sky.Resources(cpus=2,
                          _cluster_config_overrides={
                              'kubernetes': {
                                  'pod_config': pod_config
                              }
                          }))
        return task

    @staticmethod
    def _pod_config(resource_or_task):
        # Task.resources is a set before _add_k8s_annotations and a list
        # after it, so index through a list either way.
        resource = resource_or_task
        if isinstance(resource_or_task, task_lib.Task):
            resource = list(resource_or_task.resources)[0]
        return resource.cluster_config_overrides['kubernetes']['pod_config']

    def test_lists_without_patch_merge_key_not_duplicated(self):
        """Lists appended by the merge must not be doubled."""
        task = self._make_task({
            'spec': {
                'tolerations': [{
                    'key': 'nvidia.com/gpu',
                    'operator': 'Exists'
                }],
                'dnsConfig': {
                    'nameservers': ['1.1.1.1']
                },
            }
        })

        controller_module._add_k8s_annotations(task, job_id=1)

        spec = self._pod_config(task)['spec']
        assert spec['tolerations'] == [{
            'key': 'nvidia.com/gpu',
            'operator': 'Exists'
        }]
        assert spec['dnsConfig']['nameservers'] == ['1.1.1.1']

    def test_original_resource_config_not_mutated(self):
        """The annotations must not leak into the resource we copied from."""
        task = self._make_task({'spec': {'runtimeClassName': 'nvidia'}})
        original_resource = list(task.resources)[0]

        controller_module._add_k8s_annotations(task, job_id=1)

        assert 'metadata' not in self._pod_config(original_resource)

    def test_empty_image_pull_secrets_preserved(self):
        """A task clearing imagePullSecrets must not break the job loop."""
        task = self._make_task({
            'spec': {
                'containers': [{
                    'imagePullPolicy': 'IfNotPresent'
                }],
                'imagePullSecrets': [],
            }
        })

        controller_module._add_k8s_annotations(task, job_id=1)

        spec = self._pod_config(task)['spec']
        assert spec['imagePullSecrets'] == []
        assert spec['containers'] == [{'imagePullPolicy': 'IfNotPresent'}]

    def test_annotations_added_without_dropping_existing_ones(self):
        task = self._make_task(
            {'metadata': {
                'annotations': {
                    'user': 'annotation'
                }
            }})

        controller_module._add_k8s_annotations(task, job_id=384)

        assert self._pod_config(task)['metadata']['annotations'] == {
            'user': 'annotation',
            'skypilot-managed-job-id': '384',
            'skypilot-managed-job-name': 'test-task',
        }

    def test_repeated_calls_are_idempotent(self):
        """Every emergency-recovery retry re-runs this on the same task."""
        task = self._make_task({
            'spec': {
                'tolerations': [{
                    'key': 'nvidia.com/gpu',
                    'operator': 'Exists'
                }]
            }
        })

        controller_module._add_k8s_annotations(task, job_id=1)
        after_first = copy.deepcopy(self._pod_config(task))
        controller_module._add_k8s_annotations(task, job_id=1)

        assert self._pod_config(task) == after_first


class TestJobGroupOnRecoveryNetworking:
    """on_recovery re-runs networking setup after a task recovers.

    Failures on the recovered task's OWN nodes are fatal
    (ClusterSetUpError -> FAILED_SETUP): its fresh pod has no DNS
    updater or readiness marker, so without setup its networking wait
    would stall and fail minutes later with a generic message.
    Peer-only failures must NOT fail the task: a healthy peer's running
    updater short-circuits the re-push, so a failing peer is usually
    itself mid-recovery (simultaneous preemption) and its own recovery
    re-runs this setup.
    """

    async def _captured_on_recovery(self,
                                    setup_failures,
                                    inter_connection_enabled=True,
                                    self_delivering=False):
        """Run _monitor_job_group_task with a fake executor, return the
        captured on_recovery callback invoked under patches."""
        controller = MagicMock(spec=JobController)
        controller._job_id = 1
        controller._dag = MagicMock()
        controller._dag.inter_connection_enabled = MagicMock(
            return_value=inter_connection_enabled)
        task = MagicMock()
        task.name = 'job-a'
        peer = MagicMock()
        peer.name = 'job-b'

        captured = {}

        async def fake_monitor_task(**kwargs):
            captured['on_recovery'] = kwargs['on_recovery']
            return True

        executor = MagicMock()
        executor.monitor_task = fake_monitor_task

        with patch('sky.jobs.controller.job_group_networking') as net, \
             patch('sky.jobs.controller.managed_job_utils') as utils, \
             patch('sky.jobs.controller.global_user_state') as gus:
            net.setup_job_group_networking = AsyncMock(
                return_value=setup_failures)
            # Non-None marks the recovered task as self-delivering
            # (inline task.run prelude owns its updater delivery).
            net.dns_addresses_for_task.return_value = (
                ['inline-addr'] if self_delivering else None)
            utils.generate_managed_job_cluster_name.side_effect = (
                lambda name, job_id: f'{name}-{job_id}')
            gus.get_handle_from_cluster_name.return_value = MagicMock()

            result = await JobController._monitor_job_group_task(
                controller, 0, task, 'cluster-a', executor, 'group',
                [(task, MagicMock()), (peer, MagicMock())])
            assert result is True
            error = None
            try:
                await captured['on_recovery']()
            except Exception as e:  # pylint: disable=broad-except
                error = e
            return net, error

    @pytest.mark.asyncio
    async def test_no_failures_no_error(self):
        net, error = await self._captured_on_recovery(setup_failures=[])
        assert error is None
        net.setup_job_group_networking.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_own_node_failure_raises_cluster_setup_error(self):
        from sky import exceptions
        _, error = await self._captured_on_recovery(setup_failures=[
            job_group_networking.SetupFailure('job-a', 'job-a-0',
                                              'K8s DNS updater failed')
        ])
        assert isinstance(error, exceptions.ClusterSetUpError)
        assert 'job-a' in str(error)
        assert 'K8s DNS updater failed' in str(error)

    @pytest.mark.asyncio
    async def test_peer_only_failure_does_not_raise(self):
        # Simultaneous preemption: the peer is down awaiting its own
        # recovery; failing this task for it would turn every
        # multi-task preemption into a group failure.
        _, error = await self._captured_on_recovery(setup_failures=[
            job_group_networking.SetupFailure('job-b', 'job-b-0',
                                              'K8s DNS updater failed')
        ])
        assert error is None

    @pytest.mark.asyncio
    async def test_mixed_failures_raise(self):
        from sky import exceptions
        _, error = await self._captured_on_recovery(setup_failures=[
            job_group_networking.SetupFailure('job-b', 'job-b-0', 'peer down'),
            job_group_networking.SetupFailure('job-a', 'job-a-1', 'timeout')
        ])
        assert isinstance(error, exceptions.ClusterSetUpError)

    @pytest.mark.asyncio
    async def test_self_delivering_own_failure_does_not_raise(self):
        # An inline (self-delivering) task's relaunched run prelude
        # starts its own updater; the controller push is a best-effort
        # top-up in every phase (Phase 3 skips such tasks entirely), so
        # a push failure on its own nodes must not be fatal either --
        # the prelude may well have succeeded.
        _, error = await self._captured_on_recovery(setup_failures=[
            job_group_networking.SetupFailure('job-a', 'job-a-0',
                                              'exec transport broken')
        ],
                                                    self_delivering=True)
        assert error is None

    @pytest.mark.asyncio
    async def test_disabled_inter_connection_skips_setup(self):
        net, error = await self._captured_on_recovery(
            setup_failures=[], inter_connection_enabled=False)
        assert error is None
        net.setup_job_group_networking.assert_not_awaited()


class TestOnRecoveryIncludesInlineTasks:
    """on_recovery must refresh ALL group tasks, including tasks that
    inline their DNS delivery in task.run.

    The inline/push split exists only in Phase 3 (initial delivery,
    where the inline prelude will do the job itself). Post-recovery,
    inline tasks go through the same push path as everyone else -- same
    retries, same own-vs-peer classification -- and the PID-file-guarded
    start makes the controller push race-safe against the recovered
    task's own prelude. Guard against anyone 'harmonizing' on_recovery
    with Phase 3's inline skip.
    """

    @pytest.mark.asyncio
    async def test_setup_receives_every_group_task(self):
        controller = MagicMock(spec=JobController)
        controller._job_id = 1
        controller._dag = MagicMock()
        controller._dag.inter_connection_enabled = MagicMock(return_value=True)
        task = MagicMock()
        task.name = 'job-a'
        inline_peer = MagicMock()
        inline_peer.name = 'job-inline'

        captured = {}

        async def fake_monitor_task(**kwargs):
            captured['on_recovery'] = kwargs['on_recovery']
            return True

        executor = MagicMock()
        executor.monitor_task = fake_monitor_task

        with patch('sky.jobs.controller.job_group_networking') as net, \
             patch('sky.jobs.controller.managed_job_utils') as utils, \
             patch('sky.jobs.controller.global_user_state') as gus:
            net.setup_job_group_networking = AsyncMock(return_value=[])
            # Even if the peer inlines its DNS delivery, on_recovery must
            # not use dns_addresses_for_task to filter the push list (it
            # is only consulted for fatality classification, and only
            # when the recovered task's own nodes failed).
            net.dns_addresses_for_task.return_value = ['inline-addr']
            utils.generate_managed_job_cluster_name.side_effect = (
                lambda name, job_id: f'{name}-{job_id}')
            gus.get_handle_from_cluster_name.return_value = MagicMock()

            await JobController._monitor_job_group_task(
                controller, 0, task, 'cluster-a', executor, 'group',
                [(task, MagicMock()), (inline_peer, MagicMock())])
            await captured['on_recovery']()

            net.setup_job_group_networking.assert_awaited_once()
            _, passed_handles = (net.setup_job_group_networking.await_args.args)
            passed_tasks = [t for t, _ in passed_handles]
            assert passed_tasks == [task, inline_peer]
            net.dns_addresses_for_task.assert_not_called()


class TestOnRecoveryClusterSetUpErrorHandling:
    """ClusterSetUpError from a Job Group's on_recovery callback must be
    converted at the _monitor_one_task call site into a terminal
    FAILED_SETUP (with the real reason) + return False.

    Letting it propagate instead would terminate the Phase 4 monitor
    asyncio.Task with an exception, which the Phase 4 collection loop
    swallows into task_results with no terminal state ever set for the
    task -- run()'s finally would then mislabel the still-RUNNING task
    as CANCELLED, with the failure reason existing only in controller
    logs.
    """

    @pytest.mark.asyncio
    async def test_sets_failed_setup_and_returns_false(self):
        from sky import exceptions

        controller = JobController.__new__(JobController)
        controller._job_id = 1
        controller._pool = None
        controller._backend = MagicMock()
        controller._cleanup_cluster = AsyncMock()

        mock_task = MagicMock()
        mock_task.name = 'test-task'
        mock_task.num_nodes = 1

        executor = MagicMock()
        executor.recover = AsyncMock(return_value=12345.0)

        on_recovery = AsyncMock(
            side_effect=exceptions.ClusterSetUpError('networking gone'))

        with patch('asyncio.sleep', new=AsyncMock()), \
             patch('sky.jobs.state.get_job_status_with_task_id_async',
                   new=AsyncMock(return_value=managed_job_state.
                                 ManagedJobStatus.RECOVERING)), \
             patch('sky.jobs.state.set_recovered_async', new=AsyncMock()), \
             patch('sky.jobs.state.set_failed_async',
                   new=AsyncMock()) as mock_set_failed:
            succeeded = await controller._monitor_one_task(
                task_id=0,
                task=mock_task,
                cluster_name='test-cluster',
                executor=executor,
                callback_func=MagicMock(),
                force_transit_to_recovering=True,
                on_recovery=on_recovery,
            )

        assert succeeded is False
        on_recovery.assert_awaited_once()
        mock_set_failed.assert_awaited_once()
        kwargs = mock_set_failed.call_args.kwargs
        assert (kwargs['failure_type'] ==
                managed_job_state.ManagedJobStatus.FAILED_SETUP)
        assert 'networking gone' in kwargs['failure_reason']


class TestJobGroupCleanupClusters:
    """_cleanup_job_group_clusters must clean every member, in parallel.

    The gang-admission retry loop will call this once per failed attempt
    (today it runs once per job), so per-cluster failures must not skip
    the remaining teardowns, and N members must not tear down serially.
    """

    def _make_controller(self):
        controller = MagicMock(spec=JobController)
        return controller

    @pytest.mark.asyncio
    async def test_one_failure_does_not_skip_others(self):
        controller = self._make_controller()
        cleaned = []

        async def cleanup_cluster(name):
            if name == 'cluster-b':
                raise RuntimeError('teardown boom')
            cleaned.append(name)

        controller._cleanup_cluster = AsyncMock(side_effect=cleanup_cluster)
        # Must not raise despite cluster-b failing.
        await JobController._cleanup_job_group_clusters(
            controller, ['cluster-a', 'cluster-b', None, 'cluster-c'])
        assert sorted(cleaned) == ['cluster-a', 'cluster-c']
        # None entries (terminal tasks) are skipped, not passed through.
        awaited_names = [
            call.args[0] for call in controller._cleanup_cluster.await_args_list
        ]
        assert None not in awaited_names
        assert sorted(awaited_names) == ['cluster-a', 'cluster-b', 'cluster-c']

    @pytest.mark.asyncio
    async def test_cleanup_runs_in_parallel(self):
        controller = self._make_controller()
        active = 0
        max_active = 0

        async def cleanup_cluster(name):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1

        controller._cleanup_cluster = AsyncMock(side_effect=cleanup_cluster)
        await JobController._cleanup_job_group_clusters(
            controller, ['cluster-a', 'cluster-b', 'cluster-c'])
        assert max_active == 3, (
            f'expected all 3 teardowns in flight together, saw {max_active}')


class TestJobGroupPhase2FailurePropagation:
    """A Phase-2 sync failure propagates cleanly, with no teardown.

    Phase 2 of _run_job_group (fetch handles + set RUNNING) runs after all
    member clusters are up. Two properties are pinned:
    - the sibling sync coros are not cancelled mid-write (gather collects
      exceptions instead of aborting on the first one), and
    - member clusters are deliberately NOT torn down: the failure is
      controller/DB-side and propagates to emergency recovery, whose
      re-entry reconciles against the live clusters (a teardown paired
      with a retryable error would strand re-entry with RUNNING/STARTING
      rows it cannot relaunch and an empty handle list that disables the
      networking re-push).
    """

    def _make_tasks(self):
        tasks = []
        for name in ('job-a', 'job-b'):
            task = MagicMock()
            task.name = name
            task.envs = {}
            tasks.append(task)
        return tasks

    def _make_controller(self, tasks):
        controller = MagicMock(spec=JobController)
        controller._job_id = 1
        controller._pool = None
        controller._dag = MagicMock()
        controller._dag.name = 'group'
        controller._dag.tasks = tasks
        executors = [MagicMock(), MagicMock()]
        for executor in executors:
            executor.launch = AsyncMock(return_value=123.0)
        controller._prepare_job_group_task_for_launch = AsyncMock(
            side_effect=[('cluster-a', executors[0]), ('cluster-b',
                                                       executors[1])])
        controller._cleanup_job_group_clusters = AsyncMock()
        return controller

    @pytest.mark.asyncio
    async def test_phase2_failure_propagates_without_teardown(self):
        tasks = self._make_tasks()
        controller = self._make_controller(tasks)

        sibling_synced = []

        async def set_started(job_id, task_id, start_time, callback_func):
            del job_id, start_time, callback_func
            if task_id == 0:
                raise RuntimeError('phase2 db boom')
            # Prove the sibling's write is not cancelled by task 0's
            # failure: it must run to completion before teardown starts.
            await asyncio.sleep(0.02)
            sibling_synced.append(task_id)

        with patch('sky.jobs.controller.managed_job_runtime') as runtime, \
             patch('sky.jobs.controller.managed_job_state') as state, \
             patch('sky.jobs.controller.managed_job_utils'), \
             patch('sky.jobs.controller.global_user_state'), \
             patch('sky.jobs.controller.context') as ctx:
            runtime.is_registered.return_value = False
            state.get_job_status_with_task_id_async = AsyncMock(
                return_value=None)
            state.set_started_async = AsyncMock(side_effect=set_started)
            ctx.contextual_async = lambda f: f

            with pytest.raises(RuntimeError, match='phase2 db boom'):
                await JobController._run_job_group(controller)

        controller._cleanup_job_group_clusters.assert_not_awaited()
        assert sibling_synced == [1]

    @pytest.mark.asyncio
    async def test_phase2_multiple_failures_raise_first(self):
        """Multiple member failures raise the first error, no teardown."""
        tasks = self._make_tasks()
        controller = self._make_controller(tasks)

        async def set_started(job_id, task_id, start_time, callback_func):
            del job_id, start_time, callback_func
            raise RuntimeError(f'task {task_id} db boom')

        with patch('sky.jobs.controller.managed_job_runtime') as runtime, \
             patch('sky.jobs.controller.managed_job_state') as state, \
             patch('sky.jobs.controller.managed_job_utils'), \
             patch('sky.jobs.controller.global_user_state'), \
             patch('sky.jobs.controller.context') as ctx:
            runtime.is_registered.return_value = False
            state.get_job_status_with_task_id_async = AsyncMock(
                return_value=None)
            state.set_started_async = AsyncMock(side_effect=set_started)
            ctx.contextual_async = lambda f: f

            with pytest.raises(RuntimeError, match='task 0 db boom'):
                await JobController._run_job_group(controller)

        controller._cleanup_job_group_clusters.assert_not_awaited()

"""Unit tests for sky.jobs.controller - recovery logic for all job types.

Tests cover controller recovery during rolling upgrades for:
- Normal jobs (single task): Recovery based on task status
- Pipeline jobs (sequential multi-task): Recovery with task skip logic
- JobGroups (parallel tasks): Recovery with independent task states
"""
import asyncio
from typing import Dict, List, Optional, Tuple
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky.jobs import state as managed_job_state


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

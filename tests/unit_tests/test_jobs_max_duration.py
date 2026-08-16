"""Unit tests for the managed-job max_duration timeout helper."""
import time
import unittest.mock as mock

from sky import task as task_lib
from sky.jobs.controller import _build_task_specs
from sky.jobs.controller import _get_task_start_time
from sky.jobs.controller import _should_timeout


def test_no_max_duration_never_times_out():
    # A job that started long ago with no max_duration must never time out.
    assert _should_timeout(time.time() - 100000, None) is False


def test_within_duration_no_timeout():
    # A job that started 5 seconds ago with a 10h limit is within the limit.
    assert _should_timeout(time.time() - 5, '10h') is False


def test_exceeded_duration_times_out():
    # A job that started 36001 seconds ago with a 10h limit has exceeded it.
    assert _should_timeout(time.time() - 36001, '10h') is True


def test_minutes_parsing():
    # 1m = 60s; a job running 61s has exceeded it.
    assert _should_timeout(time.time() - 61, '1m') is True
    assert _should_timeout(time.time() - 59, '1m') is False


def test_seconds_parsing():
    # 90s = 90s; a job running 91s has exceeded it.
    assert _should_timeout(time.time() - 91, '90s') is True
    assert _should_timeout(time.time() - 89, '90s') is False


def test_days_parsing():
    # 1d = 86400s; a job running 86401s has exceeded it.
    assert _should_timeout(time.time() - 86401, '1d') is True
    assert _should_timeout(time.time() - 86399, '1d') is False


def test_get_task_start_time_returns_start_at():
    tasks = [{
        'task_id': 0,
        'start_at': 123.0
    }, {
        'task_id': 1,
        'start_at': 456.0
    }]
    with mock.patch(
            'sky.jobs.controller.managed_job_state.get_managed_job_tasks',
            return_value=tasks):
        assert _get_task_start_time(1, 1) == 456.0


def test_get_task_start_time_returns_none_for_unknown_task():
    tasks = [{'task_id': 0, 'start_at': 123.0}]
    with mock.patch(
            'sky.jobs.controller.managed_job_state.get_managed_job_tasks',
            return_value=tasks):
        assert _get_task_start_time(1, 99) is None


def test_get_task_start_time_returns_none_when_not_started():
    tasks = [{'task_id': 0, 'start_at': None}]
    with mock.patch(
            'sky.jobs.controller.managed_job_state.get_managed_job_tasks',
            return_value=tasks):
        assert _get_task_start_time(1, 0) is None


def _make_mock_executor(task_id: int, max_duration: str):
    """Build a mock StrategyExecutor with a single-task DAG.

    Mirrors the real StrategyExecutor: ``dag`` always contains exactly one
    task (the task being executed), while ``task_id`` is the job-wide task
    index (0, 1, 2, ... for multi-stage jobs / job groups).
    """
    task = task_lib.Task(name=f'task-{task_id}', max_duration=max_duration)
    executor = mock.Mock()
    executor.dag = mock.Mock()
    executor.dag.tasks = [task]
    executor.task_id = task_id
    executor.max_restarts_on_errors = 0
    executor.recover_on_exit_codes = []
    executor.task_specs.return_value = {}
    return executor


def test_build_task_specs_works_for_first_task():
    """_build_task_specs must work for task_id == 0."""
    executor = _make_mock_executor(task_id=0, max_duration='10h')
    specs = _build_task_specs(executor)
    assert specs['max_duration'] == '10h'


def test_build_task_specs_works_for_later_tasks():
    """Regression test: _build_task_specs must not crash for task_id >= 1.

    The executor's DAG always contains exactly one task, so indexing it by
    the job-wide task_id (executor.dag.tasks[task_id]) would raise
    IndexError for task_id >= 1 in multi-stage jobs and job groups. The
    correct lookup is executor.dag.tasks[0].
    """
    for task_id in (1, 2, 5):
        executor = _make_mock_executor(task_id=task_id, max_duration='30m')
        # Must not raise IndexError.
        specs = _build_task_specs(executor)
        assert specs['max_duration'] == '30m'


def test_build_task_specs_no_max_duration():
    """_build_task_specs must handle a task with no max_duration."""
    executor = _make_mock_executor(task_id=1, max_duration=None)
    specs = _build_task_specs(executor)
    assert specs['max_duration'] is None

"""Unit tests for the managed-job max_duration timeout helper."""
import time
import unittest.mock as mock

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
    tasks = [{'task_id': 0, 'start_at': 123.0}, {'task_id': 1, 'start_at': 456.0}]
    with mock.patch('sky.jobs.controller.managed_job_state.get_managed_job_tasks',
                    return_value=tasks):
        assert _get_task_start_time(1, 1) == 456.0


def test_get_task_start_time_returns_none_for_unknown_task():
    tasks = [{'task_id': 0, 'start_at': 123.0}]
    with mock.patch('sky.jobs.controller.managed_job_state.get_managed_job_tasks',
                    return_value=tasks):
        assert _get_task_start_time(1, 99) is None


def test_get_task_start_time_returns_none_when_not_started():
    tasks = [{'task_id': 0, 'start_at': None}]
    with mock.patch('sky.jobs.controller.managed_job_state.get_managed_job_tasks',
                    return_value=tasks):
        assert _get_task_start_time(1, 0) is None
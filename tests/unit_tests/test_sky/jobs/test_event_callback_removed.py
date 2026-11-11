"""Tests for the removal of the managed-job `event_callback` field."""
import asyncio
from unittest import mock

import pytest

from sky import dag as dag_lib
from sky import task as task_lib
from sky.jobs import utils as managed_job_utils


def _task_from_yaml(config):
    return task_lib.Task.from_yaml_config(dict(config))


def test_old_yaml_with_event_callback_still_parses():
    """Older task YAML (e.g. stored managed jobs) must still load."""
    task = _task_from_yaml({'run': 'echo hi', 'event_callback': 'echo x'})
    assert task._dropped_event_callback  # pylint: disable=protected-access
    assert 'event_callback' not in task.to_yaml_config()
    assert not hasattr(task, 'event_callback')


def test_yaml_without_event_callback_is_unflagged():
    task = _task_from_yaml({'run': 'echo hi'})
    assert not task._dropped_event_callback  # pylint: disable=protected-access


def test_task_constructor_no_longer_accepts_event_callback():
    with pytest.raises(TypeError):
        task_lib.Task(run='echo hi', event_callback='echo x')  # pylint: disable=unexpected-keyword-arg


def _dag(*tasks):
    dag = dag_lib.Dag()
    for t in tasks:
        dag.add(t)
    return dag


def test_jobs_launch_rejects_event_callback():
    dag = _dag(_task_from_yaml({'run': 'echo hi', 'event_callback': 'echo x'}))
    with pytest.raises(ValueError, match='no longer supported'):
        managed_job_utils.reject_event_callback(dag)


def test_jobs_launch_accepts_task_without_event_callback():
    managed_job_utils.reject_event_callback(_dag(_task_from_yaml({'run': 'x'})))


def test_event_callback_func_is_a_no_op_shim():
    """Kept for callers that pass a state-transition callback; runs nothing."""
    task = _task_from_yaml({'run': 'echo hi', 'event_callback': 'echo x'})
    with mock.patch.object(managed_job_utils.log_lib,
                           'run_bash_command_with_log') as run_bash:
        callback = managed_job_utils.event_callback_func(1, 0, task)
        assert asyncio.run(callback('RUNNING')) is None
    run_bash.assert_not_called()

"""Sync state reads reached from controller coroutines run off the loop."""
import asyncio
import threading
from unittest import mock

import pytest

from sky.jobs import controller as controller_module
from sky.jobs import scheduler


class _ThreadRecorder:
    """Callable that records the thread it ran on and returns a fixed value."""

    def __init__(self, result=None):
        self.result = result
        self.thread_ids = []

    def __call__(self, *args, **kwargs):
        del args, kwargs
        self.thread_ids.append(threading.get_ident())
        return self.result


def _assert_off_loop(recorder: _ThreadRecorder) -> None:
    assert recorder.thread_ids, 'helper was not called'
    assert threading.get_ident() not in recorder.thread_ids


@pytest.mark.asyncio
async def test_scheduled_launch_pool_lookup_off_loop(monkeypatch):
    get_pool = _ThreadRecorder(result='my-pool')
    monkeypatch.setattr(scheduler.state, 'get_pool_from_job_id', get_pool)
    lock = asyncio.Lock()
    async with scheduler.scheduled_launch(1, set(), lock,
                                          asyncio.Condition(lock=lock)):
        pass
    _assert_off_loop(get_pool)


@pytest.mark.asyncio
async def test_scheduled_launch_dag_lookup_off_loop(monkeypatch):
    get_pool = _ThreadRecorder(result=None)
    get_dag = _ThreadRecorder(result=None)
    monkeypatch.setattr(scheduler.state, 'get_pool_from_job_id', get_pool)
    monkeypatch.setattr(scheduler.file_content_utils, 'get_job_dag_content',
                        get_dag)
    monkeypatch.setattr(scheduler.state, 'scheduler_set_launching_async',
                        mock.AsyncMock())
    monkeypatch.setattr(scheduler.state, 'scheduler_set_alive_async',
                        mock.AsyncMock())
    lock = asyncio.Lock()
    starting: set = set()
    async with scheduler.scheduled_launch(1, starting, lock,
                                          asyncio.Condition(lock=lock)):
        assert 1 in starting
    assert not starting
    _assert_off_loop(get_pool)
    _assert_off_loop(get_dag)


@pytest.mark.asyncio
async def test_cleanup_dag_lookup_off_loop(monkeypatch):
    dag = mock.MagicMock()
    dag.tasks = []
    get_dag = _ThreadRecorder(result=dag)
    monkeypatch.setattr(controller_module, '_get_dag', get_dag)
    monkeypatch.setattr(controller_module.managed_job_state,
                        'remove_ha_recovery_script_async', mock.AsyncMock())
    monkeypatch.setattr(controller_module.managed_job_state,
                        'get_api_access_token_id', lambda job_id: None)
    manager = controller_module.ControllerManager('test-uuid')
    await manager._cleanup(job_id=1)  # pylint: disable=protected-access
    _assert_off_loop(get_dag)

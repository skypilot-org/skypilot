"""Tests for following a running managed job's logs from the log store."""
import asyncio
from typing import List, Optional
from unittest import mock

import pytest

from sky import logs
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.utils.plugin_extensions import LogDeliverySource

_RUNNING = managed_job_state.ManagedJobStatus.RUNNING
_RECOVERING = managed_job_state.ManagedJobStatus.RECOVERING
_SUCCEEDED = managed_job_state.ManagedJobStatus.SUCCEEDED
_CANCELLING = managed_job_state.ManagedJobStatus.CANCELLING


class _Reader(logs.LogReader):

    def __init__(self, result: Optional[int] = 0, raises=None):
        self.result = result
        self.raises = raises
        self.calls: List[dict] = []

    def read_cluster_job_logs(self, cluster_name, job_id, *, follow, tail):
        return None

    def follow_managed_job_logs(self, job_id, task_id, **kwargs):
        self.calls.append(dict(job_id=job_id, task_id=task_id, **kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


def _handle():
    handle = mock.MagicMock()
    handle.cluster_name = 'train-7'
    handle.cluster_name_on_cloud = 'train-7-abcd1234'
    return handle


@pytest.fixture(autouse=True)
def _reset_hooks():
    yield
    logs.register_log_reader(None)
    LogDeliverySource._undelivered_reason_func = None  # pylint: disable=protected-access


def _set_task_statuses(monkeypatch, statuses):
    rows = [(i, f'task-{i}', s, None, None) for i, s in enumerate(statuses)]
    monkeypatch.setattr(managed_job_state,
                        'get_all_task_ids_names_statuses_logs',
                        lambda job_id: rows)


def test_default_reader_does_not_follow():
    reader = _Reader()
    assert logs.LogReader.follow_managed_job_logs(
        reader, 1, 0, tail=0, is_done=lambda: True) is None


def test_no_reader_falls_back():
    assert managed_job_utils._follow_from_log_store(1, 0, 'task', _handle(),
                                                    None) is None


def test_undelivered_logs_fall_back():
    reader = _Reader()
    logs.register_log_reader(reader)
    LogDeliverySource.register(
        undelivered_reason=lambda **kwargs: 'collector not ready')
    assert managed_job_utils._follow_from_log_store(1, 0, 'task', _handle(),
                                                    None) is None
    assert not reader.calls


def test_reader_that_cannot_follow_falls_back():
    logs.register_log_reader(_Reader(result=None))
    assert managed_job_utils._follow_from_log_store(1, 0, 'task', _handle(),
                                                    10) is None


def test_reader_error_falls_back():
    logs.register_log_reader(_Reader(raises=RuntimeError('store down')))
    assert managed_job_utils._follow_from_log_store(1, 0, 'task', _handle(),
                                                    10) is None


def test_cancellation_propagates():
    logs.register_log_reader(_Reader(raises=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        managed_job_utils._follow_from_log_store(1, 0, 'task', _handle(), 10)


def test_follow_passes_identity_and_tail():
    reader = _Reader()
    logs.register_log_reader(reader)
    assert managed_job_utils._follow_from_log_store(7, 1, 'task', _handle(),
                                                    None) == 0
    call = reader.calls[0]
    assert call['job_id'] == 7
    assert call['task_id'] == 1
    assert call['task_name'] == 'task'
    assert call['cluster_name_on_cloud'] == 'train-7-abcd1234'
    assert call['tail'] == 0


def test_is_done_tracks_the_task_and_is_throttled(monkeypatch):
    seen = {}

    class _Capture(_Reader):

        def follow_managed_job_logs(self, job_id, task_id, **kwargs):
            seen['is_done'] = kwargs['is_done']
            return 0

    logs.register_log_reader(_Capture())
    clock = [100.0]
    monkeypatch.setattr(managed_job_utils.time, 'monotonic', lambda: clock[0])
    managed_job_utils._follow_from_log_store(1, 0, 'task', _handle(), None)
    is_done = seen['is_done']

    _set_task_statuses(monkeypatch, [_RUNNING])
    assert not is_done()
    # A recovery is still the same running task.
    _set_task_statuses(monkeypatch, [_RECOVERING])
    clock[0] += managed_job_utils._STORE_FOLLOW_STATUS_CHECK_SECONDS
    assert not is_done()
    # Within the throttle window the status is not re-read.
    _set_task_statuses(monkeypatch, [_SUCCEEDED])
    assert not is_done()
    clock[0] += managed_job_utils._STORE_FOLLOW_STATUS_CHECK_SECONDS
    assert is_done()
    # Once done it stays done.
    _set_task_statuses(monkeypatch, [_RUNNING])
    assert is_done()


def test_task_is_running_by_task_id(monkeypatch):
    _set_task_statuses(monkeypatch, [_SUCCEEDED, _RUNNING])
    assert not managed_job_utils._task_is_running(1, 0)
    assert managed_job_utils._task_is_running(1, 1)
    assert not managed_job_utils._task_is_running(1, 2)
    _set_task_statuses(monkeypatch, [_CANCELLING])
    assert not managed_job_utils._task_is_running(1, 0)

"""Unit tests for failing jobs whose launch request ended mid-submission.

Runs against a real temporary SQLite database for the managed jobs state, with
the API server request store stubbed.
"""
import contextlib
import time
from typing import Optional
from unittest import mock

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

import sky
from sky import dag as dag_lib
from sky.jobs import state
from sky.jobs import utils
from sky.jobs.server import core as jobs_core
from sky.server.requests import requests as requests_lib


@pytest.fixture
def _mock_managed_jobs_db_conn(tmp_path, monkeypatch):
    """Create a temporary SQLite DB for managed jobs state."""
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


def _create_inactive_job(submission_request_id: Optional[str]) -> int:
    job_id = state.set_job_info_without_job_id(
        'job',
        workspace='default',
        entrypoint='sky jobs launch',
        pool=None,
        pool_hash=None,
        user_hash='user',
        submission_request_id=submission_request_id)
    state.set_pending(job_id,
                      0,
                      'task',
                      'resources',
                      '{}',
                      eligible_at=time.time())
    return job_id


def _stub_request(monkeypatch, request: Optional[requests_lib.Request]):
    get_request = mock.Mock(return_value=request)
    monkeypatch.setattr(utils.requests_lib, 'get_request', get_request)
    return get_request


def _request(status: requests_lib.RequestStatus,
             finished_at: Optional[float] = None) -> requests_lib.Request:
    return requests_lib.Request(request_id='req-1',
                                name='jobs.launch',
                                entrypoint=lambda: None,
                                request_body=None,
                                status=status,
                                created_at=time.time(),
                                user_id='user',
                                finished_at=finished_at)


def _status_and_schedule_state(job_id: int):
    task = state.get_managed_job_tasks(job_id)[0]
    return task['status'], task['schedule_state'], task['failure_reason']


@pytest.mark.parametrize('request_row', [
    None,
    _request(requests_lib.RequestStatus.FAILED, finished_at=time.time() - 3600),
    _request(requests_lib.RequestStatus.CANCELLED,
             finished_at=time.time() - 3600),
],
                         ids=[
                             'request-gone', 'request-failed',
                             'request-cancelled'
                         ])
def test_ended_submission_fails_the_job(_mock_managed_jobs_db_conn, monkeypatch,
                                        request_row):
    job_id = _create_inactive_job('req-1')
    _stub_request(monkeypatch, request_row)

    utils.update_managed_jobs_statuses()

    status, schedule_state, failure_reason = _status_and_schedule_state(job_id)
    assert status == state.ManagedJobStatus.FAILED_CONTROLLER
    assert schedule_state == state.ManagedJobScheduleState.DONE
    assert 'req-1' in failure_reason
    assert 'never submitted' in failure_reason


@pytest.mark.parametrize(
    'request_row', [
        _request(requests_lib.RequestStatus.PENDING),
        _request(requests_lib.RequestStatus.RUNNING),
        _request(requests_lib.RequestStatus.FAILED, finished_at=time.time()),
    ],
    ids=['request-pending', 'request-running', 'request-just-ended'])
def test_live_submission_is_left_alone(_mock_managed_jobs_db_conn, monkeypatch,
                                       request_row):
    job_id = _create_inactive_job('req-1')
    _stub_request(monkeypatch, request_row)

    utils.update_managed_jobs_statuses()

    status, schedule_state, _ = _status_and_schedule_state(job_id)
    assert status == state.ManagedJobStatus.PENDING
    assert schedule_state == state.ManagedJobScheduleState.INACTIVE


def test_job_without_submission_request_is_left_alone(
        _mock_managed_jobs_db_conn, monkeypatch):
    job_id = _create_inactive_job(None)
    get_request = _stub_request(monkeypatch, None)

    utils.update_managed_jobs_statuses()

    status, schedule_state, _ = _status_and_schedule_state(job_id)
    assert status == state.ManagedJobStatus.PENDING
    assert schedule_state == state.ManagedJobScheduleState.INACTIVE
    get_request.assert_not_called()


def test_cancelled_job_of_ended_submission_is_done(_mock_managed_jobs_db_conn,
                                                   monkeypatch):
    job_id = _create_inactive_job('req-1')
    assert state.set_pending_cancelled(job_id)
    _stub_request(monkeypatch, None)

    utils.update_managed_jobs_statuses()

    status, schedule_state, _ = _status_and_schedule_state(job_id)
    assert status == state.ManagedJobStatus.CANCELLED
    assert schedule_state == state.ManagedJobScheduleState.DONE


def test_launch_records_the_submitting_request(_mock_managed_jobs_db_conn,
                                               tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_core.common_utils, 'is_in_request_context',
                        lambda: True)
    monkeypatch.setattr(jobs_core.common_utils, 'get_current_request_id',
                        lambda: 'req-launch')
    task = sky.Task(name='t', run='echo')
    with dag_lib.Dag() as dag:
        dag.add(task)
    dag.name = 'j'
    with mock.patch.object(jobs_core.managed_job_utils,
                           'is_consolidation_mode',
                           return_value=True), \
         mock.patch.object(jobs_core.backend_utils, 'get_task_resources_str',
                           return_value='r'):
        job_ids = jobs_core._maybe_submit_job_locally(str(tmp_path), dag, 2)

    assert len(job_ids) == 2
    for job_id in job_ids:
        assert state.get_submission_request_id(job_id) == 'req-launch'

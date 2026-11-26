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


_OLD = 2 * 3600


def _create_inactive_job(submission_request_id: Optional[str],
                         age_seconds: float = 0) -> int:
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
    with state._db_manager.get_engine().begin() as conn:
        conn.execute(state.spot_table.update().where(
            state.spot_table.c.spot_job_id == job_id).values(
                created_at=time.time() - age_seconds))
    return job_id


def _stub_request(monkeypatch, request: Optional[requests_lib.Request]):
    get_request = mock.Mock(return_value=request)
    monkeypatch.setattr(utils.requests_lib, 'get_request', get_request)
    return get_request


def _request(
        status: Optional[requests_lib.RequestStatus],
        finished_ago: Optional[float] = None) -> Optional[requests_lib.Request]:
    """The stored request; None for a request the store does not have."""
    if status is None:
        return None
    now = time.time()
    return requests_lib.Request(
        request_id='req-1',
        name='jobs.launch',
        entrypoint=lambda: None,
        request_body=None,
        status=status,
        created_at=now,
        user_id='user',
        finished_at=None if finished_ago is None else now - finished_ago)


def _job_row(job_id: int):
    return state.get_managed_job_tasks(job_id)[0]


@pytest.mark.parametrize(
    'status,finished_ago', [
        (None, None),
        (requests_lib.RequestStatus.FAILED, 3600),
        (requests_lib.RequestStatus.CANCELLED, 3600),
    ],
    ids=['request-gone', 'request-failed', 'request-cancelled'])
def test_ended_submission_fails_the_job(_mock_managed_jobs_db_conn, monkeypatch,
                                        status, finished_ago):
    job_id = _create_inactive_job('req-1', age_seconds=_OLD)
    _stub_request(monkeypatch, _request(status, finished_ago))

    utils.update_managed_jobs_statuses()

    row = _job_row(job_id)
    assert row['status'] == state.ManagedJobStatus.FAILED_CONTROLLER
    assert row['schedule_state'] == state.ManagedJobScheduleState.DONE
    assert 'req-1' in row['failure_reason']
    assert 'never submitted' in row['failure_reason']


@pytest.mark.parametrize(
    'status,finished_ago', [
        (requests_lib.RequestStatus.PENDING, None),
        (requests_lib.RequestStatus.RUNNING, None),
        (requests_lib.RequestStatus.FAILED, 0),
    ],
    ids=['request-pending', 'request-running', 'request-just-ended'])
def test_live_submission_is_left_alone(_mock_managed_jobs_db_conn, monkeypatch,
                                       status, finished_ago):
    job_id = _create_inactive_job('req-1')
    _stub_request(monkeypatch, _request(status, finished_ago))

    utils.update_managed_jobs_statuses()

    row = _job_row(job_id)
    assert row['status'] == state.ManagedJobStatus.PENDING
    assert row['schedule_state'] == state.ManagedJobScheduleState.INACTIVE


def test_unseen_request_of_a_recent_job_is_left_alone(
        _mock_managed_jobs_db_conn, monkeypatch):
    # The request may be running on another API server with its own store.
    job_id = _create_inactive_job('req-1', age_seconds=60)
    _stub_request(monkeypatch, None)

    utils.update_managed_jobs_statuses()

    row = _job_row(job_id)
    assert row['status'] == state.ManagedJobStatus.PENDING
    assert row['schedule_state'] == state.ManagedJobScheduleState.INACTIVE


def test_job_without_submission_request_is_left_alone(
        _mock_managed_jobs_db_conn, monkeypatch):
    job_id = _create_inactive_job(None)
    get_request = _stub_request(monkeypatch, None)

    utils.update_managed_jobs_statuses()

    row = _job_row(job_id)
    assert row['status'] == state.ManagedJobStatus.PENDING
    assert row['schedule_state'] == state.ManagedJobScheduleState.INACTIVE
    get_request.assert_not_called()


def test_cancelled_job_of_ended_submission_is_done(_mock_managed_jobs_db_conn,
                                                   monkeypatch):
    job_id = _create_inactive_job('req-1', age_seconds=_OLD)
    assert state.set_pending_cancelled(job_id)
    _stub_request(monkeypatch, None)

    utils.update_managed_jobs_statuses()

    row = _job_row(job_id)
    assert row['status'] == state.ManagedJobStatus.CANCELLED
    assert row['schedule_state'] == state.ManagedJobScheduleState.DONE


def test_jobs_of_one_launch_share_one_request_lookup(_mock_managed_jobs_db_conn,
                                                     monkeypatch):
    job_ids = [
        _create_inactive_job('req-1', age_seconds=_OLD) for _ in range(3)
    ]
    get_request = _stub_request(monkeypatch, None)

    utils.update_managed_jobs_statuses()

    get_request.assert_called_once()
    for job_id in job_ids:
        assert (_job_row(job_id)['status'] ==
                state.ManagedJobStatus.FAILED_CONTROLLER)


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

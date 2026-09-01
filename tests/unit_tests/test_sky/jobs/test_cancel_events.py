"""Unit tests for cancel-request attribution in the job event log.

Runs against a real temporary SQLite database (fixture pattern from
test_recovery_metrics_state.py), so the event rows that cancel_jobs_by_id
writes are asserted as the dashboard's Events table would read them.
"""
import contextlib
import time

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import models
from sky.jobs import constants as managed_job_constants
from sky.jobs import state
from sky.jobs import utils


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


@pytest.fixture
def _signal_dir(tmp_path, monkeypatch):
    """Keep the cancel signal file out of the real ~/.sky directory.

    Also stubs out the status reconciliation that cancel_jobs_by_id runs
    before signalling: it inspects whether the seeded controller PID is alive
    on this machine, which has nothing to do with the attribution under test
    and would make the outcome depend on the host's process table.
    """
    signal_dir = tmp_path / 'signals'
    signal_dir.mkdir()
    monkeypatch.setattr(managed_job_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(signal_dir))
    monkeypatch.setattr(utils, 'update_managed_jobs_statuses',
                        lambda *args, **kwargs: None)
    yield signal_dir


def _seed_running_job(engine, job_id: int, workspace: str = 'default') -> None:
    with engine.connect() as conn:
        conn.execute(state.job_info_table.insert().values(
            spot_job_id=job_id,
            name=f'job-{job_id}',
            workspace=workspace,
            # A controller pid with a start time marks the job as running on a
            # modern multi-job controller, so the cancel takes the consolidated
            # signal-file path rather than the legacy one.
            controller_pid=12345,
            controller_pid_started_at=time.time(),
            schedule_state=state.ManagedJobScheduleState.ALIVE.value,
        ))
        conn.execute(state.spot_table.insert().values(
            job_name=f'job-{job_id}',
            status='RUNNING',
            spot_job_id=job_id,
            task_id=0,
        ))
        conn.commit()


def _event_reasons(job_id: int):
    return [event['reason'] for event in state.get_job_events(job_id)]


def test_explicit_requester_recorded(_mock_managed_jobs_db_conn, _signal_dir):
    """A requester passed in (the codegen path) reaches the event log."""
    _seed_running_job(_mock_managed_jobs_db_conn, 1)

    msg = utils.cancel_jobs_by_id(job_ids=[1],
                                  current_workspace='default',
                                  cancel_request_info=utils.CancelRequestInfo(
                                      user_hash='abcd1234',
                                      user_name='alice',
                                      request_id='req-1'))

    assert 'scheduled to be cancelled' in msg
    assert (_signal_dir / '1').exists()
    assert _event_reasons(1) == [
        'Cancellation requested by user alice (request ID: req-1)'
    ]


def test_requester_from_request_context(_mock_managed_jobs_db_conn, _signal_dir,
                                        monkeypatch):
    """With no requester passed in, the ambient API request context is used.

    This is the in-process path, where cancel_jobs_by_id runs inside the API
    request that asked for the cancellation.
    """
    _seed_running_job(_mock_managed_jobs_db_conn, 1)
    monkeypatch.setattr(utils.common_utils, 'is_in_request_context',
                        lambda: True)
    monkeypatch.setattr(utils.common_utils, 'get_current_user',
                        lambda: models.User(id='abcd1234', name='alice'))
    monkeypatch.setattr(utils.common_utils, 'get_current_request_id',
                        lambda: 'req-1')

    utils.cancel_jobs_by_id(job_ids=[1], current_workspace='default')

    assert _event_reasons(1) == [
        'Cancellation requested by user alice (request ID: req-1)'
    ]


def test_no_requester_records_no_event(_mock_managed_jobs_db_conn, _signal_dir,
                                       monkeypatch):
    """A cancel with no identifiable requester adds no event."""
    _seed_running_job(_mock_managed_jobs_db_conn, 1)
    monkeypatch.setattr(utils.common_utils, 'is_in_request_context',
                        lambda: False)

    utils.cancel_jobs_by_id(job_ids=[1], current_workspace='default')

    assert not _event_reasons(1)
    assert (_signal_dir / '1').exists()


def test_skipped_job_is_not_attributed(_mock_managed_jobs_db_conn, _signal_dir):
    """A job outside the active workspace is neither cancelled nor attributed."""
    _seed_running_job(_mock_managed_jobs_db_conn, 1, workspace='other')

    msg = utils.cancel_jobs_by_id(
        job_ids=[1],
        current_workspace='default',
        cancel_request_info=utils.CancelRequestInfo(user_name='alice'))

    assert 'No job to cancel' in msg
    assert not _event_reasons(1)
    assert not (_signal_dir / '1').exists()

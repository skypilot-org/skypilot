"""Regression test for `_consolidated_launch` returncode propagation.

`_consolidated_launch` historically discarded the return code of the
controller subprocess. A non-zero exit (e.g. interpreter mismatch, DB
unreachable, OOM during scheduler_set_waiting) was silently swallowed,
leaving a PENDING+INACTIVE job row in the DB while the API returned
SUCCEEDED to the user. These tests pin the new behavior: a non-zero
returncode cancels the jobs that no controller has claimed and raises, so
the request is marked FAILED.
"""

import contextlib
from unittest import mock

import filelock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from sky import exceptions
from sky.jobs import state
from sky.jobs.server import core as jobs_core


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


def _make_mock_backend(returncode: int) -> mock.MagicMock:
    """A CloudVmRayBackend stub that returns the given returncode."""
    backend = mock.MagicMock()
    backend.run_on_head.return_value = returncode
    # sync_file_mounts is a no-op for our purposes.
    backend.sync_file_mounts = mock.MagicMock()
    return backend


def _patch_consolidated_launch_deps(monkeypatch, tmp_path,
                                    returncode: int) -> None:
    """Wire up the minimum surface needed for `_consolidated_launch` to run.

    We mock everything `_consolidated_launch` reaches for *before* the
    `run_on_head` call so the function gets to the returncode check with
    a controlled value.
    """
    fake_handle = mock.MagicMock()
    monkeypatch.setattr(
        'sky.backends.backend_utils.is_controller_accessible',
        lambda **kwargs: fake_handle,
    )
    backend = _make_mock_backend(returncode)
    monkeypatch.setattr(
        'sky.backends.backend_utils.get_backend_from_handle',
        lambda _h: backend,
    )
    # _consolidated_launch asserts the backend is a CloudVmRayBackend; the
    # asserted type comes from `sky.backends`. Make isinstance() happy.
    import sky.backends as sky_backends
    monkeypatch.setattr(sky_backends, 'CloudVmRayBackend', mock.MagicMock)
    # Keep the submission log directory out of the real ~/sky_logs.
    monkeypatch.setattr(jobs_core.skylet_constants, 'SKY_LOGS_DIRECTORY',
                        str(tmp_path / 'sky_logs'))


def _make_controller_task() -> mock.MagicMock:
    task = mock.MagicMock()
    task.run = 'echo "test run script"'
    task.envs = {}
    task.file_mounts = None
    task.storage_mounts = None
    return task


def _seed_pending_job(name: str = 'job') -> int:
    """Seed a job the way launch does before it runs the scheduler script:
    PENDING with schedule_state INACTIVE."""
    job_id = state.set_job_info_without_job_id(name=name,
                                               workspace='default',
                                               entrypoint='ep',
                                               pool=None,
                                               pool_hash=None,
                                               user_hash='user1')
    state.set_pending(job_id,
                      task_id=0,
                      task_name='task0',
                      resources_str='{}',
                      metadata='{}')
    return job_id


def _set_waiting(job_id: int) -> None:
    state.scheduler_set_waiting([job_id], 'dag', 'user-yaml', 'env', None, 100)


def _claim(engine, job_id: int) -> None:
    """Mark the job claimed by a controller, as get_waiting_job_async does."""
    with engine.begin() as conn:
        conn.execute(state.job_info_table.update().where(
            state.job_info_table.c.spot_job_id == job_id).values(
                schedule_state=state.ManagedJobScheduleState.LAUNCHING.value))


def _launch(job_ids):
    return jobs_core._consolidated_launch(mock.MagicMock(),
                                          _make_controller_task(),
                                          job_ids=job_ids)


def test_zero_returncode_succeeds(monkeypatch, tmp_path):
    """Happy path: returncode 0 returns job_ids and cancels nothing."""
    _patch_consolidated_launch_deps(monkeypatch, tmp_path, returncode=0)
    cancel = mock.MagicMock()
    monkeypatch.setattr(jobs_core.managed_job_state, 'set_pending_cancelled',
                        cancel)

    job_ids, handle = _launch([7])
    assert job_ids == [7]
    assert handle is not None
    cancel.assert_not_called()


def test_failure_before_waiting_cancels_inactive_job(_mock_managed_jobs_db_conn,
                                                     monkeypatch, tmp_path):
    """The script failed before scheduler_set_waiting committed: the job is
    PENDING+INACTIVE, which nothing would ever pick up. It gets cancelled
    and the request fails."""
    _patch_consolidated_launch_deps(monkeypatch, tmp_path, returncode=42)
    job_id = _seed_pending_job()

    with pytest.raises(exceptions.CommandError) as excinfo:
        _launch([job_id])

    assert excinfo.value.returncode == 42
    assert f'cancelled job(s) {job_id}' in excinfo.value.error_msg
    assert 'safe to retry' in excinfo.value.error_msg
    assert 'submit-job-' in excinfo.value.error_msg
    assert state.get_status(job_id) == state.ManagedJobStatus.CANCELLED
    events = state.get_job_events(job_id)
    assert any(e['new_status'] == state.ManagedJobStatus.CANCELLED and
               'exit code 42' in e['reason'] for e in events), events


def test_failure_after_waiting_claims_job_from_scheduler(
        _mock_managed_jobs_db_conn, monkeypatch, tmp_path):
    """The script failed after scheduler_set_waiting committed: the job is
    WAITING. Cancelling moves it to DONE, so no controller can claim it."""
    _patch_consolidated_launch_deps(monkeypatch, tmp_path, returncode=1)
    job_id = _seed_pending_job()
    _set_waiting(job_id)

    with pytest.raises(exceptions.CommandError) as excinfo:
        _launch([job_id])

    assert f'cancelled job(s) {job_id}' in excinfo.value.error_msg
    assert state.get_status(job_id) == state.ManagedJobStatus.CANCELLED
    assert (state.get_job_schedule_state(job_id) ==
            state.ManagedJobScheduleState.DONE)


def test_claimed_job_is_left_to_run(_mock_managed_jobs_db_conn, monkeypatch,
                                    tmp_path):
    """A controller already claimed the job, so it will run. It is left
    alone, and the error tells the user how to cancel it."""
    _patch_consolidated_launch_deps(monkeypatch, tmp_path, returncode=1)
    cancelled_id = _seed_pending_job('cancelled')
    claimed_id = _seed_pending_job('claimed')
    _set_waiting(claimed_id)
    _claim(_mock_managed_jobs_db_conn, claimed_id)

    with pytest.raises(exceptions.CommandError) as excinfo:
        _launch([cancelled_id, claimed_id])

    msg = excinfo.value.error_msg
    assert f'could not cancel job(s) {claimed_id}' in msg
    assert f'`sky jobs cancel {claimed_id}`' in msg
    assert 'safe to retry' not in msg
    assert state.get_status(cancelled_id) == state.ManagedJobStatus.CANCELLED
    assert state.get_status(claimed_id) == state.ManagedJobStatus.PENDING
    assert (state.get_job_schedule_state(claimed_id) ==
            state.ManagedJobScheduleState.LAUNCHING)


def test_cancel_hint_lists_ids_the_cli_accepts(monkeypatch, tmp_path):
    """`sky jobs cancel` takes space-separated integers, not the compact
    `10-12` range form, so the hint must spell out each id."""
    _patch_consolidated_launch_deps(monkeypatch, tmp_path, returncode=1)
    monkeypatch.setattr(jobs_core.managed_job_state, 'set_pending_cancelled',
                        lambda job_id, reason: False)

    with pytest.raises(exceptions.CommandError) as excinfo:
        _launch([10, 11, 12])

    assert 'could not cancel job(s) 10-12' in excinfo.value.error_msg
    assert '`sky jobs cancel 10 11 12`' in excinfo.value.error_msg


def test_cancel_error_still_fails_request(monkeypatch, tmp_path):
    """The DB may be why the script failed. If the cancel raises too, the
    request still fails with the cancel hint instead of the DB error."""
    _patch_consolidated_launch_deps(monkeypatch, tmp_path, returncode=1)

    def _raise(job_id, reason):
        raise RuntimeError('database is locked')

    monkeypatch.setattr(jobs_core.managed_job_state, 'set_pending_cancelled',
                        _raise)

    with pytest.raises(exceptions.CommandError) as excinfo:
        _launch([7])

    assert excinfo.value.returncode == 1
    assert '`sky jobs cancel 7`' in excinfo.value.error_msg

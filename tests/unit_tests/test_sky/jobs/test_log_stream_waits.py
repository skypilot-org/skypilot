"""How long managed-job log streaming waits for the controller.

stream_logs_by_id waits for the controller in four places: for the job to
start, for a failed task to restart, for the next task, and after a tail that
ended without the job finishing. The first wait matches the controller's
start check. The other three match the controller's status-check gap for the
job, which the runtime of its cluster may set.
"""
# pylint: disable=protected-access
import types
from unittest import mock

import pytest

from sky import backends
from sky import exceptions
from sky.jobs import runtime
from sky.jobs import state
from sky.jobs import utils
from sky.skylet import job_lib
from sky.skylet import log_lib

_RUNTIME_GAP = 4


class _Stop(Exception):
    """Ends stream_logs_by_id at the wait under test."""


def _handle(has_ray):
    handle = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
    handle.provision_runtime_metadata = types.SimpleNamespace(has_ray=has_ray)
    return handle


@pytest.fixture(name='job_state')
def _job_state(monkeypatch):
    monkeypatch.setattr(runtime, '_runtimes', [])
    job_state = mock.MagicMock(wraps=state)
    job_state.ManagedJobStatus = state.ManagedJobStatus
    job_state.get_num_tasks.return_value = 1
    job_state.get_status.return_value = state.ManagedJobStatus.RUNNING
    job_state.get_latest_task_id_status.return_value = (
        0, state.ManagedJobStatus.RUNNING)
    job_state.get_pool_from_job_id.return_value = None
    job_state.get_task_name.return_value = 'train'
    job_state.get_task_specs.return_value = {'max_restarts_on_errors': 1}
    monkeypatch.setattr(utils, 'managed_job_state', job_state)
    monkeypatch.setattr(log_lib, 'start_orphan_watchdog', mock.MagicMock())
    monkeypatch.setattr(utils.rich_utils, 'safe_status', mock.MagicMock())
    monkeypatch.setattr(utils, 'read_provision_status_from_log',
                        lambda path, pos, msg: (pos, msg))
    monkeypatch.setattr(utils, '_parked_launch_reason', lambda *args: None)
    return job_state


@pytest.fixture(name='sleeps')
def _sleeps(monkeypatch):
    """Records each sleep; the first sleep longer than a second stops the
    stream, so a test sees exactly one wait for the controller."""
    recorded = []

    def fake_sleep(seconds):
        recorded.append(seconds)
        if seconds > utils._PROVISION_LOG_POLL_GAP_SECONDS:
            raise _Stop()

    monkeypatch.setattr(utils.time, 'sleep', fake_sleep)
    return recorded


def _cluster(monkeypatch, kind, returncode, job_status):
    """Puts the job on a cluster and makes its tail end with returncode.

    kind is 'runtime' (a Ray-free cluster whose runtime asks for
    _RUNTIME_GAP), 'runtime-default' (the same runtime, answering None) or
    'classic' (a Ray-backed cluster, which runtimes never handle). Returns
    the gap the controller checks the job at.
    """
    if kind == 'classic':
        handle = _handle(has_ray=True)
        monkeypatch.setattr(backends.CloudVmRayBackend, 'tail_logs',
                            mock.Mock(return_value=returncode))
        monkeypatch.setattr(backends.CloudVmRayBackend, 'get_job_status',
                            mock.Mock(return_value={1: job_status}))
        expected_gap = utils.JOB_STATUS_CHECK_GAP_SECONDS
    else:
        handle = _handle(has_ray=False)
        gap = _RUNTIME_GAP if kind == 'runtime' else None
        runtime.register(
            types.SimpleNamespace(
                owns=lambda h: h is handle,
                tail_logs=mock.Mock(return_value=returncode),
                get_job_status=mock.Mock(return_value=(job_status, None)),
                get_status_check_gap_seconds=mock.Mock(return_value=gap)))
        expected_gap = (_RUNTIME_GAP if kind == 'runtime' else
                        utils.JOB_STATUS_CHECK_GAP_SECONDS)
    monkeypatch.setattr(utils.global_user_state, 'get_handle_from_cluster_name',
                        lambda cluster_name: handle)
    return expected_gap


_KINDS = ['runtime', 'runtime-default', 'classic']


@pytest.mark.parametrize('job_status', [
    state.ManagedJobStatus.PENDING, state.ManagedJobStatus.STARTING,
    state.ManagedJobStatus.RECOVERING
])
@pytest.mark.parametrize('runtime_registered', [False, True])
def test_wait_for_the_job_to_start(job_state, sleeps, monkeypatch, job_status,
                                   runtime_registered):
    """While the job is not running, the stream re-reads its status every
    JOB_STARTED_STATUS_CHECK_GAP_SECONDS, the gap at which the controller
    checks whether a job it launched has started, and reads the controller
    log every second in between. There is no handle to ask yet."""
    if runtime_registered:
        runtime.register(
            types.SimpleNamespace(get_status_check_gap_seconds=mock.Mock(
                return_value=30)))
    monkeypatch.setattr(utils.global_user_state, 'get_handle_from_cluster_name',
                        lambda cluster_name: None)
    job_state.get_status.return_value = job_status
    job_state.get_latest_task_id_status.side_effect = [(0, job_status), _Stop()]

    with pytest.raises(_Stop):
        utils.stream_logs_by_id(9)

    assert all(s == utils._PROVISION_LOG_POLL_GAP_SECONDS for s in sleeps)
    assert sum(sleeps) == utils.JOB_STARTED_STATUS_CHECK_GAP_SECONDS


@pytest.mark.usefixtures('job_state')
@pytest.mark.parametrize('kind', _KINDS)
def test_wait_for_a_failed_task_to_restart(sleeps, monkeypatch, kind):
    """The controller notices the failure at its next status check, so the
    stream re-reads the job's status at the job's status-check gap."""
    gap = _cluster(monkeypatch, kind, exceptions.JobExitCode.FAILED.value,
                   job_lib.JobStatus.FAILED)

    with pytest.raises(_Stop):
        utils.stream_logs_by_id(9)

    assert sleeps == [gap]


@pytest.mark.parametrize('kind', _KINDS)
def test_wait_for_the_next_task(job_state, sleeps, monkeypatch, kind):
    gap = _cluster(monkeypatch, kind, exceptions.JobExitCode.SUCCEEDED.value,
                   job_lib.JobStatus.SUCCEEDED)
    job_state.get_num_tasks.return_value = 2

    with pytest.raises(_Stop):
        utils.stream_logs_by_id(9)

    assert sleeps == [gap]


@pytest.mark.usefixtures('job_state')
@pytest.mark.parametrize('kind', _KINDS)
@pytest.mark.parametrize('returncode,job_status', [
    (255, None),
    (exceptions.JobExitCode.SUCCEEDED.value, job_lib.JobStatus.CANCELLED),
])
def test_wait_after_a_tail_that_did_not_finish_the_job(sleeps, monkeypatch,
                                                       kind, returncode,
                                                       job_status):
    """A tail that fails, or ends on a cancelled job that is still running
    in the jobs database, waits three of the job's status-check gaps for the
    controller to record what happened."""
    gap = _cluster(monkeypatch, kind, returncode, job_status)

    with pytest.raises(_Stop):
        utils.stream_logs_by_id(9)

    assert sleeps == [3 * gap]

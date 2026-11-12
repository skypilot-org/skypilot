"""Tests for graceful shutdown of the API server uvicorn wrapper.

The server worker sets ``block_requests`` early during graceful shutdown so
that new requests are rejected while in-flight requests drain. If the drain
step then fails to complete (e.g. the request backend / database is
unreachable), the worker must still exit so it can be restarted and clear the
block -- otherwise it stays up rejecting every request forever.
"""
import signal
from unittest import mock

import filelock
import pytest
import uvicorn

from sky.jobs import constants as managed_job_constants
from sky.jobs import scheduler as managed_job_scheduler
from sky.server import uvicorn as sky_uvicorn


async def _dummy_app(scope, receive, send):  # pylint: disable=unused-argument
    pass


def _make_server() -> sky_uvicorn.Server:
    return sky_uvicorn.Server(uvicorn.Config(app=_dummy_app))


def _patch_fast(monkeypatch) -> None:
    # Skip the real sleeps, the block-requests side effect and the stop of
    # this host's job controllers.
    monkeypatch.setattr(sky_uvicorn.time, 'sleep', lambda *_a, **_k: None)
    monkeypatch.setattr(sky_uvicorn.state, 'set_block_requests',
                        lambda *_a, **_k: None)
    monkeypatch.setattr(managed_job_scheduler, 'stop_local_job_controllers',
                        lambda **_k: None)


def test_graceful_shutdown_exits_when_backend_unreachable(monkeypatch):
    """A drain failure must not prevent the worker from exiting."""
    server = _make_server()
    _patch_fast(monkeypatch)

    backend = mock.Mock()
    backend.get_shutdown_active_requests.side_effect = RuntimeError('db down')
    monkeypatch.setattr(sky_uvicorn.request_storage, 'get_request_backend',
                        lambda: backend)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    assert server.should_exit is True


def test_graceful_shutdown_exits_when_no_requests(monkeypatch):
    """The happy path still exits cleanly."""
    server = _make_server()
    _patch_fast(monkeypatch)

    backend = mock.Mock()
    backend.get_shutdown_active_requests.return_value = []
    monkeypatch.setattr(sky_uvicorn.request_storage, 'get_request_backend',
                        lambda: backend)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    assert server.should_exit is True


def test_graceful_shutdown_exits_when_coordinator_lock_times_out(monkeypatch):
    """A worker that cannot acquire the coordinator lock still exits."""
    server = _make_server()
    _patch_fast(monkeypatch)

    class _StuckLock:

        def __init__(self, *_a, **_k):
            pass

        def acquire(self, *_a, **_k):
            raise filelock.Timeout('lock held')

    monkeypatch.setattr(sky_uvicorn.filelock, 'FileLock', _StuckLock)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    assert server.should_exit is True


@pytest.fixture
def draining(monkeypatch, tmp_path):
    """The drain mark of a server that runs the host's controller pool."""
    path = tmp_path / 'job_controllers_draining'
    monkeypatch.setattr(managed_job_constants, 'CONTROLLERS_DRAINING_PATH',
                        str(path))
    monkeypatch.setattr(managed_job_scheduler, 'runs_local_controller_pool',
                        lambda: True)
    return path


def _patch_requests(monkeypatch, on_going, launches) -> None:
    """Sets the on-going (request_id, name) pairs, one list per look, and
    which of the launches (request_id -> bool) a job controller submitted."""
    backend = mock.Mock()
    backend.get_shutdown_active_requests.side_effect = on_going
    monkeypatch.setattr(sky_uvicorn.request_storage, 'get_request_backend',
                        lambda: backend)
    records = []
    for request_id, by_controller in launches.items():
        record = mock.Mock(request_id=request_id)
        record.request_body.is_launched_by_jobs_controller = by_controller
        records.append(record)
    monkeypatch.setattr(
        sky_uvicorn.requests_lib, 'get_request_tasks', lambda request_filter: [
            record for record in records
            if record.request_id in request_filter.request_ids
        ])


@pytest.mark.parametrize('is_draining,by_controller,waited',
                         [(True, True, False), (True, False, True),
                          (False, True, True)])
def test_shutdown_does_not_wait_for_draining_controllers_launches(
        monkeypatch, draining, is_draining, by_controller, waited):
    """Such a launch ends only with its controller, which the server stops
    after the wait: waiting for it always took the whole grace period."""
    if is_draining:
        draining.touch()
    server = _make_server()
    _patch_requests(monkeypatch,
                    on_going=[[('launch-1', 'sky.launch')], []],
                    launches={'launch-1': by_controller})
    pauses = []
    monkeypatch.setattr(sky_uvicorn.time, 'sleep', pauses.append)
    interrupt = mock.Mock()
    monkeypatch.setattr(server, 'interrupt_request_for_retry', interrupt)

    server._wait_requests()  # pylint: disable=protected-access

    assert bool(pauses) is waited
    interrupt.assert_not_called()


@pytest.mark.parametrize('wait_deadline,cancel_record_timeout', [(1040.0, 40.0),
                                                                 (990.0, 0.0)])
def test_shutdown_stops_job_controllers_then_their_launches(
        monkeypatch, draining, wait_deadline, cancel_record_timeout):
    """A live controller reads an interrupted launch as a failed one. A cancel
    gets what is left of the time requests get to finish."""
    draining.touch()
    server = _make_server()
    _patch_requests(monkeypatch,
                    on_going=[[('of-controller', 'sky.launch'),
                               ('of-user', 'sky.launch'),
                               ('status', 'sky.status')]],
                    launches={
                        'of-controller': True,
                        'of-user': False
                    })
    calls = []
    monkeypatch.setattr(
        managed_job_scheduler, 'stop_local_job_controllers',
        lambda cancel_record_timeout_seconds: calls.append(
            ('controllers', cancel_record_timeout_seconds)))
    monkeypatch.setattr(
        server, 'interrupt_request_for_retry',
        lambda request_id, should_retry: calls.append(
            (request_id, should_retry)))
    monkeypatch.setattr(sky_uvicorn.time, 'time', lambda: 1000.0)

    server._stop_job_controllers(wait_deadline)  # pylint: disable=protected-access

    assert calls == [('controllers', cancel_record_timeout),
                     ('of-controller', False)]


def test_wait_for_requests_ends_before_the_grace_period():
    """The process is killed when the grace period ends, so the exit that
    follows the wait needs a part of it."""
    grace_period = sky_uvicorn.server_constants.GRACE_PERIOD_SECONDS
    # pylint: disable=protected-access
    assert 0 < sky_uvicorn._WAIT_REQUESTS_TIMEOUT_SECONDS < grace_period


def test_shutdown_keeps_launches_of_controllers_it_could_not_stop(
        monkeypatch, draining):
    draining.touch()
    server = _make_server()
    _patch_fast(monkeypatch)
    _patch_requests(monkeypatch,
                    on_going=[[('of-controller', 'sky.launch')]] * 2,
                    launches={'of-controller': True})
    monkeypatch.setattr(managed_job_scheduler, 'stop_local_job_controllers',
                        mock.Mock(side_effect=RuntimeError('still running')))
    interrupt = mock.Mock()
    monkeypatch.setattr(server, 'interrupt_request_for_retry', interrupt)

    server._graceful_shutdown(signal.SIGTERM, None)  # pylint: disable=protected-access

    interrupt.assert_not_called()
    assert server.should_exit is True


def test_main_process_exit_drains_job_controllers(monkeypatch, draining):
    """A single-process server's exit stops its controllers from claiming; a
    worker exit does not, since the supervisor also restarts workers."""
    monkeypatch.setattr(sky_uvicorn.threading, 'Thread', mock.Mock())

    worker = _make_server()
    worker.handle_exit(signal.SIGTERM, None)
    assert not draining.exists()

    main = _make_server()
    main.is_main_process = True
    main.handle_exit(signal.SIGTERM, None)
    assert draining.exists()


def test_supervisor_exit_drains_job_controllers(monkeypatch, draining):
    # Keep the supervisor from replacing pytest's signal handlers.
    monkeypatch.setattr(signal, 'signal', lambda *_a: None)
    supervisor = sky_uvicorn.SlowStartMultiprocess(uvicorn.Config(
        app=_dummy_app, workers=2),
                                                   target=lambda sockets: None,
                                                   sockets=[])

    supervisor.terminate_all()

    assert draining.exists()

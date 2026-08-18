"""Tests for per-worker self-quarantine of stalled API server workers.

The load-bearing claims are (a) pausing really does stop the worker from
accepting on the shared listening socket and resuming really does bring it
back, and (b) the serving-worker floor cannot be breached, because both are
the difference between routing around a slow worker and denying service.
"""
import asyncio
import os
import socket
from unittest import mock

import pytest

from sky.server import worker_health

# ---------------------------------------------------------------------------
# QuarantineSlots
# ---------------------------------------------------------------------------


def _slots(tmp_path, total, ratio=0.5) -> worker_health.QuarantineSlots:
    return worker_health.QuarantineSlots(total,
                                         ratio,
                                         directory=str(tmp_path / 'q'))


def test_slots_floor_math(tmp_path):
    assert _slots(tmp_path, 128, 0.5).max_quarantined == 64
    # Rounds the serving floor up, so the pool never rounds in favour of
    # masking one worker too many.
    assert _slots(tmp_path, 3, 0.5).max_quarantined == 1
    # ratio=0 still keeps one worker serving.
    assert _slots(tmp_path, 4, 0.0).max_quarantined == 3
    assert _slots(tmp_path, 1, 0.5).max_quarantined == 0


def test_slots_claim_and_release(tmp_path):
    slots = _slots(tmp_path, 4, 0.5)
    with mock.patch.object(worker_health, '_pid_is_alive', return_value=True):
        assert slots.try_claim(111) is True
        assert slots.count() == 1
        slots.release(111)
        assert slots.count() == 0


def test_slots_refuse_claim_at_floor(tmp_path):
    slots = _slots(tmp_path, 2, 0.5)  # at most 1 masked
    with mock.patch.object(worker_health, '_pid_is_alive', return_value=True):
        assert slots.try_claim(111) is True
        assert slots.try_claim(222) is False
        # The loser must not leave its marker behind, or the pool shrinks
        # for good.
        assert slots.count() == 1


def test_slots_single_worker_never_claims(tmp_path):
    slots = _slots(tmp_path, 1, 0.5)
    assert slots.try_claim(111) is False
    assert slots.count() == 0


def test_slots_reap_dead_worker_markers(tmp_path):
    """A worker that dies while masked must not shrink the pool forever."""
    slots = _slots(tmp_path, 4, 0.5)
    live = {111}
    with mock.patch.object(worker_health,
                           '_pid_is_alive',
                           side_effect=lambda pid: pid in live):
        assert slots.try_claim(111) is True
        # The supervisor replaced pid 222 with a fresh worker long ago.
        os.close(
            os.open(os.path.join(str(tmp_path / 'q'), '222'),
                    os.O_CREAT | os.O_WRONLY, 0o600))
        assert slots.count() == 1
    assert not os.path.exists(os.path.join(str(tmp_path / 'q'), '222'))


def test_slots_ignore_non_pid_files(tmp_path):
    slots = _slots(tmp_path, 4, 0.5)
    os.makedirs(str(tmp_path / 'q'), exist_ok=True)
    with open(os.path.join(str(tmp_path / 'q'), 'README'), 'w') as f:
        f.write('not a pid')
    assert slots.count() == 0


def test_slots_missing_directory_counts_zero(tmp_path):
    assert _slots(tmp_path, 4, 0.5).count() == 0


def test_pid_is_alive_defaults_to_alive_on_error():
    """Never drop a live worker's marker because psutil hiccuped."""
    with mock.patch.object(worker_health.psutil,
                           'pid_exists',
                           side_effect=RuntimeError('boom')):
        assert worker_health._pid_is_alive(1) is True


# ---------------------------------------------------------------------------
# AcceptGate -- against a real asyncio server on a real socket
# ---------------------------------------------------------------------------


class _FakeUvicornServer:

    def __init__(self, servers, connections=None):
        self.servers = servers
        self.server_state = mock.Mock()
        self.server_state.connections = connections or set()


@pytest.mark.asyncio
async def test_accept_gate_pause_and_resume_real_socket():
    """Pausing must actually stop accept(), and resuming must restore it."""
    loop = asyncio.get_running_loop()
    accepted = []

    class _Proto(asyncio.Protocol):

        def connection_made(self, transport):
            accepted.append(transport)

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = await loop.create_server(_Proto, sock=sock)
    gate = worker_health.AcceptGate(_FakeUvicornServer([server]))
    assert gate.supported() is True

    try:
        # Baseline: the server accepts.
        reader_writer = await asyncio.open_connection('127.0.0.1', port)
        await asyncio.sleep(0.05)
        assert len(accepted) == 1
        reader_writer[1].close()

        gate.pause()
        # The TCP handshake still completes -- the kernel backlog absorbs
        # it -- but no protocol is created, which is what "not serving"
        # means for a worker sharing a listening socket with its siblings.
        paused_conn = await asyncio.open_connection('127.0.0.1', port)
        await asyncio.sleep(0.1)
        assert len(accepted) == 1, 'paused worker must not accept'

        gate.resume()
        await asyncio.sleep(0.1)
        assert len(accepted) == 2, 'resumed worker must accept the backlog'
        paused_conn[1].close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_accept_gate_resume_is_idempotent():
    """A double resume must not register two readers for one socket."""
    loop = asyncio.get_running_loop()
    accepted = []

    class _Proto(asyncio.Protocol):

        def connection_made(self, transport):
            accepted.append(transport)

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = await loop.create_server(_Proto, sock=sock)
    gate = worker_health.AcceptGate(_FakeUvicornServer([server]))
    try:
        gate.pause()
        gate.resume()
        gate.resume()
        conn = await asyncio.open_connection('127.0.0.1', port)
        await asyncio.sleep(0.05)
        assert len(accepted) == 1
        conn[1].close()
    finally:
        server.close()
        await server.wait_closed()


def test_accept_gate_reports_unsupported_runtime():
    """If asyncio's internals move, say so instead of half-applying."""

    class _Opaque:
        pass

    gate = worker_health.AcceptGate(_FakeUvicornServer([_Opaque()]))
    assert gate.supported() is False


def test_drain_closes_http_but_not_websockets():
    """WebSockets are long-lived tunnels; draining them breaks sessions."""
    http_cls = worker_health._http_protocol_classes()
    assert http_cls, 'uvicorn must expose at least one HTTP protocol class'

    http_conn = mock.Mock(spec=http_cls[0])
    ws_conn = mock.Mock()  # not an instance of any HTTP protocol class
    server = _FakeUvicornServer([], connections={http_conn, ws_conn})

    drained = worker_health.AcceptGate(server).drain_idle_connections()
    assert drained == 1
    http_conn.shutdown.assert_called_once_with()
    ws_conn.shutdown.assert_not_called()


def test_drain_survives_a_broken_connection():
    http_cls = worker_health._http_protocol_classes()
    bad = mock.Mock(spec=http_cls[0])
    bad.shutdown.side_effect = RuntimeError('already gone')
    good = mock.Mock(spec=http_cls[0])
    server = _FakeUvicornServer([], connections={bad, good})
    assert worker_health.AcceptGate(server).drain_idle_connections() == 1


# ---------------------------------------------------------------------------
# WorkerHealthGate policy
# ---------------------------------------------------------------------------


def _gate(tmp_path, monkeypatch, **cfg) -> worker_health.WorkerHealthGate:
    config = worker_health.QuarantineConfig(enabled=True,
                                            lag_threshold=1.0,
                                            stall_budget=2.0,
                                            cooldown=10.0,
                                            min_serving_ratio=0.5,
                                            **cfg)
    gate = worker_health.WorkerHealthGate(
        _FakeUvicornServer([]),
        total_workers=4,
        config=config,
        slots=_slots(tmp_path, 4, config.min_serving_ratio))
    gate._loop = mock.Mock()
    # The real loop runs the callback on its own thread; run it inline so
    # the tests see the accept-path effects the gate actually schedules.
    gate._loop.call_soon_threadsafe.side_effect = lambda fn: fn()
    gate._accept = mock.Mock()
    # The peer pids these tests compete with are made up, so slot liveness
    # has to be stubbed for all of them.
    monkeypatch.setattr(worker_health, '_pid_is_alive', lambda pid: True)
    return gate


def _stall(gate, seconds: float, now: float = 1000.0) -> float:
    """Feed the gate ``seconds`` of continuous event-loop stall."""
    ticks = int(seconds / worker_health._SAMPLE_INTERVAL_SECONDS)
    for _ in range(ticks):
        now += worker_health._SAMPLE_INTERVAL_SECONDS
        # Loop hasn't beaten in a long time => big lag.
        gate._last_beat = now - 60.0
        gate._tick(now)
    return now


def _healthy(gate, seconds: float, now: float) -> float:
    ticks = int(seconds / worker_health._SAMPLE_INTERVAL_SECONDS)
    for _ in range(ticks):
        now += worker_health._SAMPLE_INTERVAL_SECONDS
        gate._last_beat = now
        gate._tick(now)
    return now


def test_gate_quarantines_after_sustained_stall(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    now = _stall(gate, 1.5)
    assert gate._quarantined is False, 'must not fire before the budget'
    _stall(gate, 1.0, now)
    assert gate._quarantined is True
    assert gate._slots.count() == 1


def test_gate_ignores_a_brief_spike(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    now = _stall(gate, 1.0)
    _healthy(gate, 30.0, now)
    assert gate._quarantined is False
    assert gate._budget == 0.0


def test_gate_accumulates_across_repeated_stalls(tmp_path, monkeypatch):
    """Many short stalls are the real failure shape, not one long block.

    The budget drains slower than it fills precisely so a worker that
    stalls for a second every few seconds still ends up masked out.
    """
    gate = _gate(tmp_path, monkeypatch)
    now = 1000.0
    for _ in range(6):
        now = _stall(gate, 1.0, now)
        now = _healthy(gate, 1.0, now)
    assert gate._quarantined is True


def test_gate_refuses_to_breach_the_serving_floor(tmp_path, monkeypatch):
    """The last serving workers stay in rotation, however slow they are."""
    gate = _gate(tmp_path, monkeypatch)
    # Two peers already masked out of a 4-worker replica.
    gate._slots.try_claim(9001)
    gate._slots.try_claim(9002)
    _stall(gate, 10.0)
    assert gate._quarantined is False
    assert gate._slots.count() == 2


def test_gate_resumes_after_cooldown_and_backs_off(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    now = _stall(gate, 3.0)
    assert gate._quarantined is True

    # Still inside the cooldown.
    now += 5.0
    gate._last_beat = now
    gate._tick(now)
    assert gate._quarantined is True

    now += 10.0
    gate._last_beat = now
    gate._tick(now)
    assert gate._quarantined is False
    assert gate._slots.count() == 0, 'slot must go back to the pool'
    gate._accept.resume.assert_called_once_with()

    # A worker that keeps falling over is wedged, not busy: back off.
    now = _stall(gate, 3.0, now)
    assert gate._quarantined is True
    assert gate._cooldown_seconds() == 20.0


def test_gate_cooldown_backoff_is_capped(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    gate._cooldown_multiplier = worker_health._MAX_COOLDOWN_MULTIPLIER
    gate._quarantined = True
    gate._quarantined_at = 0.0
    gate._maybe_resume(now=10_000.0)
    assert (gate._cooldown_multiplier == worker_health._MAX_COOLDOWN_MULTIPLIER)


def test_gate_stop_releases_the_slot(tmp_path, monkeypatch):
    """A restarting worker must not be counted against the floor by itself."""
    gate = _gate(tmp_path, monkeypatch)
    _stall(gate, 3.0)
    assert gate._slots.count() == 1
    gate.stop()
    assert gate._slots.count() == 0


def test_gate_publishes_serving_gauge(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    with mock.patch.object(worker_health.metrics_utils,
                           'SKY_APISERVER_WORKER_SERVING') as gauge:
        _stall(gate, 3.0)
        gauge.labels.return_value.set.assert_called_once_with(0)


def test_gate_start_declines_when_disabled(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    gate._config.enabled = False
    assert gate.start(loop=mock.Mock()) is False


def test_gate_start_declines_without_spare_workers(tmp_path, monkeypatch):
    config = worker_health.QuarantineConfig(enabled=True)
    gate = worker_health.WorkerHealthGate(_FakeUvicornServer([]),
                                          total_workers=1,
                                          config=config,
                                          slots=_slots(tmp_path, 1))
    assert gate.start(loop=mock.Mock()) is False


def test_gate_start_declines_on_unsupported_runtime(tmp_path, monkeypatch):
    gate = _gate(tmp_path, monkeypatch)
    gate._accept = mock.Mock()
    gate._accept.supported.return_value = False
    assert gate.start(loop=mock.Mock()) is False


def test_maybe_start_skips_single_worker_servers():
    assert worker_health.maybe_start(
        _FakeUvicornServer([]), total_workers=1, loop=mock.Mock()) is None


def test_maybe_start_disabled_by_default():
    """Off unless explicitly turned on."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop('SKYPILOT_WORKER_QUARANTINE_ENABLED', None)
        assert worker_health.maybe_start(
            _FakeUvicornServer([]), total_workers=8, loop=mock.Mock()) is None


def test_config_from_env_reads_overrides():
    env = {
        'SKYPILOT_WORKER_QUARANTINE_ENABLED': 'true',
        'SKYPILOT_WORKER_QUARANTINE_LAG_SECONDS': '2.5',
        'SKYPILOT_WORKER_QUARANTINE_STALL_BUDGET_SECONDS': '7',
        'SKYPILOT_WORKER_QUARANTINE_COOLDOWN_SECONDS': '45',
        'SKYPILOT_WORKER_QUARANTINE_MIN_SERVING_RATIO': '0.75',
    }
    with mock.patch.dict(os.environ, env):
        config = worker_health.QuarantineConfig.from_env()
    assert config.enabled is True
    assert config.lag_threshold == 2.5
    assert config.stall_budget == 7.0
    assert config.cooldown == 45.0
    assert config.min_serving_ratio == 0.75


def test_config_from_env_ignores_garbage():
    with mock.patch.dict(os.environ,
                         {'SKYPILOT_WORKER_QUARANTINE_LAG_SECONDS': 'soon'}):
        assert worker_health.QuarantineConfig.from_env().lag_threshold == 1.0


def test_gate_stays_masked_while_the_loop_is_still_wedged(
        tmp_path, monkeypatch):
    """Never advertise a worker as back before its loop has actually run.

    The resume callback is scheduled on the event loop. If the loop never
    gets to it the worker is by definition unable to serve, so the slot,
    the gauge and the quarantine flag must all stay put.
    """
    gate = _gate(tmp_path, monkeypatch)
    # A loop that accepts callbacks and never runs them.
    scheduled = []
    gate._loop.call_soon_threadsafe.side_effect = scheduled.append
    now = _stall(gate, 3.0)
    assert gate._quarantined is True

    for _ in range(5):
        now += 60.0
        gate._last_beat = now
        gate._tick(now)

    assert gate._quarantined is True
    assert gate._slots.count() == 1, 'slot must not be released early'
    # One quarantine callback and exactly one resume attempt, not one per
    # tick.
    assert len(scheduled) == 2

    # The loop finally drains its queue.
    scheduled[-1]()
    assert gate._quarantined is False
    assert gate._slots.count() == 0


def test_gate_throttles_repeated_claim_attempts(tmp_path, monkeypatch):
    """A replica-wide stall must not turn into a slot-scan storm."""
    gate = _gate(tmp_path, monkeypatch)
    gate._slots.try_claim(9001)
    gate._slots.try_claim(9002)  # floor reached for a 4-worker replica
    calls = []
    real_try_claim = gate._slots.try_claim

    def _counting_try_claim(pid):
        calls.append(pid)
        return real_try_claim(pid)

    monkeypatch.setattr(gate._slots, 'try_claim', _counting_try_claim)
    # 10s of continuous stall at 0.5s per tick would be 16 refused claims
    # without the retry window.
    _stall(gate, 10.0)
    assert gate._quarantined is False
    assert len(calls) == 1


def test_publish_initial_serving_state_is_a_noop_when_disabled(monkeypatch):
    """The gauge must stay absent when quarantine is off.

    Consumers read an absent family as "no worker here is masked"; a
    partially-populated one would be read as the opposite.
    """
    monkeypatch.delenv('SKYPILOT_WORKER_QUARANTINE_ENABLED', raising=False)
    with mock.patch.object(worker_health.metrics_utils,
                           'SKY_APISERVER_WORKER_SERVING') as gauge:
        worker_health.publish_initial_serving_state()
        gauge.labels.assert_not_called()


def test_publish_initial_serving_state_marks_serving_when_enabled(monkeypatch):
    monkeypatch.setenv('SKYPILOT_WORKER_QUARANTINE_ENABLED', 'true')
    with mock.patch.object(worker_health.metrics_utils,
                           'SKY_APISERVER_WORKER_SERVING') as gauge:
        worker_health.publish_initial_serving_state()
        gauge.labels.return_value.set.assert_called_once_with(1)


def test_accept_gate_without_servers_is_unsupported():
    """Reporting 'supported' with nothing to pause would mask nothing."""
    assert worker_health.AcceptGate(_FakeUvicornServer([])).supported() is False


def test_reset_quarantine_dir_is_safe_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_health, 'QUARANTINE_DIR',
                        str(tmp_path / 'never-created'))
    worker_health.reset_quarantine_dir()  # must not raise

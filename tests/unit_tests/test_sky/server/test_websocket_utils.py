"""Tests for the SSH-over-websocket proxy instrumentation."""
import asyncio
import struct
from unittest import mock

import pytest

from sky.server import websocket_utils


def _sampler(enabled=True):
    """A sampler with a stub histogram, so observations are inspectable."""
    with mock.patch.object(websocket_utils.metrics_utils, 'METRICS_ENABLED',
                           enabled):
        sampler = websocket_utils._BackendTurnaroundSampler('portforward')
    observed = []
    sampler._histogram = mock.Mock(observe=observed.append)
    return sampler, observed


def test_small_write_pairs_with_next_read():
    sampler, observed = _sampler()
    sampler.on_write(64)
    sampler.on_read()
    assert len(observed) == 1
    assert observed[0] >= 0


def test_only_first_read_is_paired():
    """A long echo split across reads must contribute one sample, not many."""
    sampler, observed = _sampler()
    sampler.on_write(64)
    sampler.on_read()
    sampler.on_read()
    sampler.on_read()
    assert len(observed) == 1


def test_oversized_write_is_not_sampled():
    """A paste or an scp is not a request/response exchange."""
    sampler, observed = _sampler()
    sampler.on_write(
        websocket_utils._BackendTurnaroundSampler._MAX_WRITE_BYTES + 1)
    sampler.on_read()
    assert observed == []


def test_empty_write_is_not_sampled():
    sampler, observed = _sampler()
    sampler.on_write(0)
    sampler.on_read()
    assert observed == []


def test_pipelined_writes_keep_the_oldest_stamp():
    """Typing into a stalled backend must still be measured.

    The stream is ordered, so the first read answers the first write. Keeping
    the oldest stamp is both the correct attribution and the only way a stall
    -- when a user keeps typing into the lag -- shows up at all.
    """
    sampler, observed = _sampler()
    with mock.patch.object(websocket_utils.time,
                           'monotonic',
                           side_effect=[10.0, 10.9]):
        sampler.on_write(64)  # stamped at 10.0
        sampler.on_write(64)  # pipelined; must NOT reset the stamp
        sampler.on_read()  # at 10.9
    assert observed == [
        pytest.approx(0.9)
    ], ('a stalled backend with continued typing must produce a slow sample')


def test_pipelining_does_not_wedge_the_sampler():
    """After a paired round, the next exchange is sampled independently."""
    sampler, observed = _sampler()
    sampler.on_write(64)
    sampler.on_write(64)
    sampler.on_read()
    assert len(observed) == 1
    sampler.on_write(64)
    sampler.on_read()
    assert len(observed) == 2


def test_bulk_write_clears_an_outstanding_stamp():
    """A paste mid-keystroke: the next read answers the paste, not the key."""
    sampler, observed = _sampler()
    sampler.on_write(64)
    sampler.on_write(
        websocket_utils._BackendTurnaroundSampler._MAX_WRITE_BYTES + 1)
    sampler.on_read()
    assert observed == []


def test_read_without_a_write_is_ignored():
    """Unsolicited backend output (a `yes` loop, a keepalive) is not a reply."""
    sampler, observed = _sampler()
    sampler.on_read()
    assert observed == []


def test_stale_reply_is_discarded():
    """Past the cap, a read is far likelier to be unrelated than a slow echo."""
    sampler, observed = _sampler()
    cap = websocket_utils._BackendTurnaroundSampler._MAX_PENDING_SECONDS
    with mock.patch.object(websocket_utils.time,
                           'monotonic',
                           side_effect=[100.0, 100.0 + cap + 0.1]):
        sampler.on_write(64)
        sampler.on_read()
    assert observed == []


def test_elapsed_is_measured_not_guessed():
    sampler, observed = _sampler()
    with mock.patch.object(websocket_utils.time,
                           'monotonic',
                           side_effect=[10.0, 10.025]):
        sampler.on_write(64)
        sampler.on_read()
    assert observed == [pytest.approx(0.025)]


def test_disabled_sampler_never_touches_the_histogram():
    """Metrics off is the default; the sampler must cost nothing there."""
    sampler, observed = _sampler(enabled=False)
    sampler.on_write(64)
    sampler.on_read()
    assert observed == []


class _FakeWebSocket:
    """Minimal stand-in for fastapi.WebSocket over a fixed inbound script."""

    def __init__(self, inbound):
        self._inbound = list(inbound)
        self.sent = []
        self.closed = False

    async def iter_bytes(self):
        for message in self._inbound:
            yield message

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _framed(payload: bytes) -> bytes:
    return struct.pack('!B',
                       websocket_utils.SSHMessageType.REGULAR_DATA) + payload


@pytest.mark.asyncio
async def test_proxy_records_turnaround_end_to_end():
    """Drive the real proxy loop: a keystroke in, an echo out, one sample."""
    websocket = _FakeWebSocket([_framed(b'a')])
    written = []
    # One echo, then EOF so backend_to_websocket terminates.
    reads = [b'a', b'']

    async def read_from_backend():
        await asyncio.sleep(0)
        return reads.pop(0) if reads else b''

    async def write_to_backend(data):
        written.append(data)

    async def close_backend():
        pass

    observed = []
    real_init = websocket_utils._BackendTurnaroundSampler.__init__

    def init_with_stub(self, path):
        real_init(self, path)
        self._enabled = True
        self._histogram = mock.Mock(observe=observed.append)

    with mock.patch.object(websocket_utils._BackendTurnaroundSampler,
                           '__init__', init_with_stub):
        await websocket_utils.run_websocket_proxy(
            websocket,
            read_from_backend=read_from_backend,
            write_to_backend=write_to_backend,
            close_backend=close_backend,
            timestamps_supported=True,
        )

    # The type byte is stripped before the backend sees the keystroke.
    assert written == [b'a']
    assert len(observed) == 1


@pytest.mark.asyncio
async def test_proxy_does_not_pair_a_heartbeat_ping():
    """A PING is echoed by the server and never forwarded, so it is not a
    write to the backend and must not produce a turnaround sample."""
    ping = struct.pack('!BI', websocket_utils.SSHMessageType.PINGPONG, 7)
    websocket = _FakeWebSocket([ping])

    async def read_from_backend():
        await asyncio.sleep(0)
        return b''

    async def write_to_backend(data):
        raise AssertionError(f'PING must not reach the backend: {data!r}')

    async def close_backend():
        pass

    observed = []
    real_init = websocket_utils._BackendTurnaroundSampler.__init__

    def init_with_stub(self, path):
        real_init(self, path)
        self._enabled = True
        self._histogram = mock.Mock(observe=observed.append)

    with mock.patch.object(websocket_utils._BackendTurnaroundSampler,
                           '__init__', init_with_stub):
        await websocket_utils.run_websocket_proxy(
            websocket,
            read_from_backend=read_from_backend,
            write_to_backend=write_to_backend,
            close_backend=close_backend,
            timestamps_supported=True,
        )

    assert websocket.sent == [ping]
    assert observed == []


def test_heartbeat_help_string_says_it_is_not_keystroke_latency():
    """The whole point of the rename: nobody should read this metric as
    keystroke latency or as covering the pod leg."""
    from sky.metrics import utils as metrics_utils
    documentation = (metrics_utils.SKY_APISERVER_WEBSOCKET_SSH_LATENCY_SECONDS.
                     _documentation)
    assert 'NOT keystroke latency' in documentation
    assert 'sky_apiserver_ssh_backend_turnaround_seconds' in documentation

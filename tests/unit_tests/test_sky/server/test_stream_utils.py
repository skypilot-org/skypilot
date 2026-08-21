"""Tests for log streaming of a request that parks mid-execution."""
import aiofiles
import pytest

from sky.server import stream_utils
from sky.server.requests import requests as requests_lib
from sky.utils import message_utils
from sky.utils import rich_utils


def _decoded_controls(chunks):
    """(control, message) for every rich-status payload in the chunks."""
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            is_payload, decoded = message_utils.decode_payload(
                line, raise_for_mismatch=False)
            if not is_payload:
                continue
            control, msg = rich_utils.Control.decode(decoded)
            if control is not None:
                out.append((control, msg))
    return out


def _status_sequence(monkeypatch, sequence):
    """Serve a fixed (status, status_msg) sequence to the tail loop."""
    remaining = list(sequence)

    async def get_request_status_async(request_id, include_msg=False):
        del request_id, include_msg
        status, msg = remaining.pop(0) if remaining else sequence[-1]
        return requests_lib.StatusWithMsg(status, msg)

    monkeypatch.setattr(requests_lib, 'get_request_status_async',
                        get_request_status_async)


async def _collect(log_path, plain_logs=False):
    chunks = []
    async with aiofiles.open(log_path, 'rb') as f:
        async for chunk in stream_utils._tail_log_file(f,
                                                       request_id='rid',
                                                       plain_logs=plain_logs,
                                                       follow=True,
                                                       polling_interval=0):
            chunks.append(chunk)
    return chunks


@pytest.mark.asyncio
async def test_parked_request_status_is_pushed_to_an_attached_stream(
        monkeypatch, tmp_path):
    """A client attached before the park must not be left on a stale line.

    A parked request stops writing to its log, so a client that is already
    tailing it sees nothing more: before this it kept displaying whatever it had
    streamed last, for as long as the wait lasted and no matter how the reason
    changed. The parked message is pushed into the stream instead.
    """
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    waiting = requests_lib.RequestStatus.WAITING
    _status_sequence(monkeypatch, [
        (waiting, 'Pending (Queue: q, Position: 1) (waiting to resume)'),
        (waiting, 'Pending (Queue: q, Position: 1) (waiting to resume)'),
        (waiting, 'Pending (Queue: q, Position: 0) (waiting to resume)'),
        (requests_lib.RequestStatus.SUCCEEDED, None),
    ])

    controls = _decoded_controls(await _collect(log_path))

    messages = [
        msg for control, msg in controls if control is rich_utils.Control.INIT
    ]
    # Both distinct reasons reached the client, and the repeat did not.
    assert messages == [
        '[dim]Pending (Queue: q, Position: 1) (waiting to resume)[/dim]',
        '[dim]Pending (Queue: q, Position: 0) (waiting to resume)[/dim]',
    ]


@pytest.mark.asyncio
async def test_parked_request_status_is_pushed_as_plain_text(
        monkeypatch, tmp_path):
    """With plain logs the parked message is a line, not a control frame."""
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    _status_sequence(monkeypatch, [
        (requests_lib.RequestStatus.WAITING, 'Pending (Queue: q)'),
        (requests_lib.RequestStatus.SUCCEEDED, None),
    ])

    chunks = await _collect(log_path, plain_logs=True)

    assert any('Pending (Queue: q)' in chunk for chunk in chunks)
    assert not _decoded_controls(chunks)


@pytest.mark.asyncio
async def test_resume_lets_the_request_drive_its_own_status_again(
        monkeypatch, tmp_path):
    """After a resume, the same reason may be pushed again if it parks anew.

    The parked message is de-duplicated only while the request stays parked;
    a resumed request writes its own status, so the next park has to be able to
    report the same reason rather than being silently suppressed.
    """
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    waiting = requests_lib.RequestStatus.WAITING
    running = requests_lib.RequestStatus.RUNNING
    msg = 'Pending (Queue: q, Position: 0) (waiting to resume)'
    _status_sequence(monkeypatch, [
        (waiting, msg),
        (running, None),
        (waiting, msg),
        (requests_lib.RequestStatus.SUCCEEDED, None),
    ])

    controls = _decoded_controls(await _collect(log_path))

    inits = [m for c, m in controls if c is rich_utils.Control.INIT]
    assert len(inits) == 2, inits

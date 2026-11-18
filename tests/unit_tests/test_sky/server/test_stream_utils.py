"""Tests for request log streaming."""
import asyncio
import collections
import gzip
import json
import random
import types

import aiofiles
import fastapi
import pytest

from sky.server import constants as server_constants
from sky.server import stream_utils
from sky.server.requests import log_provider
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

    # Both distinct reasons reached the client, and the repeat did not.
    expected = [
        '[dim]Pending (Queue: q, Position: 1) (waiting to resume)[/dim]',
        '[dim]Pending (Queue: q, Position: 0) (waiting to resume)[/dim]',
    ]
    inits = [
        msg for control, msg in controls if control is rich_utils.Control.INIT
    ]
    assert inits == expected
    # START is what paints it: an INIT alone leaves the client's Live stopped,
    # so the message would be pushed and never drawn.
    assert [control for control, _ in controls
           ].count(rich_utils.Control.START) == len(expected)
    # ...and each is applied, not merely initialized: a client that still holds
    # a status reuses it on INIT without picking up the new text, so the UPDATE
    # is what the user actually reads.
    updates = [
        msg for control, msg in controls if control is rich_utils.Control.UPDATE
    ]
    assert updates == expected


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


@pytest.mark.asyncio
async def test_parked_message_repeats_after_any_status_change(
        monkeypatch, tmp_path):
    """A reason may be reported again once the request has left WAITING.

    The de-dup is per parked stretch. Resetting it only on RUNNING meant a
    resume that went WAITING -> PENDING -> WAITING between two polls kept the
    old message suppressed, leaving the client on whatever the request emitted
    while it ran.
    """
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    waiting = requests_lib.RequestStatus.WAITING
    msg = 'Pending (Queue: q) (waiting to resume)'
    _status_sequence(monkeypatch, [
        (waiting, msg),
        (requests_lib.RequestStatus.PENDING, None),
        (waiting, msg),
        (requests_lib.RequestStatus.SUCCEEDED, None),
    ])

    controls = _decoded_controls(await _collect(log_path))

    inits = [m for c, m in controls if c is rich_utils.Control.INIT]
    assert inits == [f'[dim]{msg}[/dim]'] * 2, inits


# --- Status checks of a parked request -------------------------------------

_WAITING = requests_lib.RequestStatus.WAITING
_RUNNING = requests_lib.RequestStatus.RUNNING
_SUCCEEDED = requests_lib.RequestStatus.SUCCEEDED
_CANCELLED = requests_lib.RequestStatus.CANCELLED
_CAP = stream_utils._WAITING_STATUS_CHECK_MAX_INTERVAL
_LOW = 1 - stream_utils._WAITING_STATUS_CHECK_JITTER
# The loop sleeps at least this long, so a check can land this much after it
# is due.
_TICK = stream_utils._IDLE_READ_INTERVAL
_EPS = 1e-6


class _VirtualTime:
    """A clock that the tail loop's sleeps advance instantly.

    Lets a test run an hour-long park in milliseconds and see exactly when
    each status check happened.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = []
        self._events = []

    def time(self) -> float:
        return self.now

    def get_event_loop(self) -> '_VirtualTime':
        return self

    def at(self, when, callback) -> None:
        """Run ``callback`` once the clock reaches ``when``."""
        self._events.append((when, callback))

    async def sleep(self, delay) -> None:
        self.sleeps.append(delay)
        self.now += delay
        assert self.now < 3600, 'the stream did not end'
        for event in [e for e in self._events if e[0] <= self.now]:
            self._events.remove(event)
            event[1]()
        await _REAL_SLEEP(0)


_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def clock(monkeypatch):
    clock = _VirtualTime()
    monkeypatch.setattr(
        stream_utils, 'asyncio',
        types.SimpleNamespace(sleep=clock.sleep,
                              get_event_loop=clock.get_event_loop))
    monkeypatch.setattr(stream_utils, 'random', random.Random(0))
    return clock


def _status_timeline(monkeypatch, clock, timeline, should_retry=False):
    """Serve the status that ``timeline`` holds at the current time.

    ``timeline`` is a list of (start time, status, status_msg); a None status
    means the request record is gone. Returns the times of the status checks.
    """
    checks = []

    async def get_request_status_async(request_id, include_msg=False):
        del request_id, include_msg
        checks.append(clock.now)
        current = [entry for entry in timeline if entry[0] <= clock.now][-1]
        _, status, msg = current
        if status is None:
            return None
        return requests_lib.StatusWithMsg(status, msg)

    async def get_request_async(request_id, fields=None):
        del fields
        return types.SimpleNamespace(name='sky.launch',
                                     request_id=request_id,
                                     should_retry=should_retry)

    monkeypatch.setattr(requests_lib, 'get_request_status_async',
                        get_request_status_async)
    monkeypatch.setattr(requests_lib, 'get_request_async', get_request_async)
    return checks


async def _collect_timed(clock, log_path, polling_interval=1):
    """(time, chunk) for every chunk the tail yields."""
    out = []
    async with aiofiles.open(log_path, 'rb') as f:
        async for chunk in stream_utils._tail_log_file(
                f, request_id='rid', follow=True,
                polling_interval=polling_interval):
            out.append((clock.now, chunk))
    return out


def _intervals(times):
    return [b - a for a, b in zip(times, times[1:])]


def test_the_check_interval_doubles_to_the_cap_while_waiting(monkeypatch):
    monkeypatch.setattr(stream_utils, 'random', random.Random(0))
    interval = stream_utils._StatusCheckInterval(1)
    assert interval.current == 1 and not interval.parked

    for nominal in [2, 4, 8, _CAP, _CAP, _CAP]:
        interval.on_waiting()
        assert interval.parked
        # Jittered after the cap, and only downwards.
        assert nominal * _LOW - _EPS <= interval.current <= nominal + _EPS


def test_the_check_interval_resets_to_the_base(monkeypatch):
    monkeypatch.setattr(stream_utils, 'random', random.Random(0))
    interval = stream_utils._StatusCheckInterval(1)
    for _ in range(6):
        interval.on_waiting()

    interval.reset()

    assert interval.current == 1 and not interval.parked
    # The next park backs off from the start again.
    interval.on_waiting()
    assert interval.current <= 2


@pytest.mark.parametrize('base', [0, 0.1, 30])
def test_the_check_interval_is_never_below_the_base(monkeypatch, base):
    monkeypatch.setattr(stream_utils, 'random', random.Random(0))
    interval = stream_utils._StatusCheckInterval(base)
    for _ in range(20):
        interval.on_waiting()
        assert interval.current >= base
        assert interval.current <= max(base, _CAP)


def test_streams_that_park_together_do_not_check_in_step(monkeypatch):
    """Jitter at the cap spreads the checks of streams parked together.

    With the jitter applied before the cap, most intervals come out at
    exactly the cap, and streams that park at the same moment keep reading
    the DB in the same instant. Here 200 streams park at once.
    """
    monkeypatch.setattr(stream_utils, 'random', random.Random(0))
    streams = 200
    per_second = collections.Counter()
    at_cap = 0
    total = 0
    for _ in range(streams):
        interval = stream_utils._StatusCheckInterval(1)
        now = 1.0
        while now < 600:
            interval.on_waiting()
            now += interval.current
            total += 1
            at_cap += abs(interval.current - _CAP) < 0.05
            if now >= 120:
                per_second[int(now)] += 1

    assert at_cap / total < 0.05
    # About streams / 8 checks land in an average second; in lock-step, a
    # second would hold most of the streams.
    mean = sum(per_second.values()) / len(per_second)
    assert max(per_second.values()) < 2 * mean, (max(per_second.values()), mean)


@pytest.mark.asyncio
async def test_a_parked_stream_backs_off_its_status_checks(
        monkeypatch, tmp_path, clock):
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    checks = _status_timeline(monkeypatch, clock, [
        (0, _WAITING, 'Waiting for the cluster lock'),
        (600, _SUCCEEDED, None),
    ])
    planned = []
    on_waiting = stream_utils._StatusCheckInterval.on_waiting

    def record_planned(self):
        on_waiting(self)
        planned.append(self.current)

    monkeypatch.setattr(stream_utils._StatusCheckInterval, 'on_waiting',
                        record_planned)

    await _collect_timed(clock, log_path)

    waiting_checks = [t for t in checks if t < 600]
    gaps = _intervals(waiting_checks)
    # Each check happens when it is due. Waking only on a coarse tick would
    # round the jittered intervals and line the streams up again.
    for gap, due in zip(gaps, planned):
        assert due - _EPS <= gap <= due + _TICK + _EPS, (gap, due)
    # Was one check per second: about 600 here.
    assert 600 / (_CAP + _TICK) <= len(waiting_checks) <= 600 / (_CAP * _LOW)
    assert max(gaps) <= _CAP + _TICK + _EPS
    # Past the ramp (1 + 2 + 4 + 8 s), every gap is a jittered cap.
    assert min(g for t, g in zip(waiting_checks, gaps) if t > 20) >= (
        _CAP * _LOW - _EPS)
    # The log is read about once a second rather than ten times.
    assert len([s for s in clock.sleeps if s > 0]) < 1.5 * 600
    # The terminal status is still seen within one capped interval.
    assert checks[-1] <= 600 + _CAP + _TICK


@pytest.mark.asyncio
async def test_a_running_stream_keeps_its_check_interval(
        monkeypatch, tmp_path, clock):
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    checks = _status_timeline(monkeypatch, clock, [
        (0, _RUNNING, None),
        (60, _SUCCEEDED, None),
    ])

    await _collect_timed(clock, log_path)

    assert all(1 - _EPS <= gap <= 1 + _TICK + _EPS
               for gap in _intervals(checks)), _intervals(checks)
    assert max(clock.sleeps) == _TICK
    assert checks[-1] <= 60 + 1 + _TICK + _EPS


@pytest.mark.asyncio
async def test_a_resumed_request_is_checked_at_the_base_interval_again(
        monkeypatch, tmp_path, clock):
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    checks = _status_timeline(monkeypatch, clock, [
        (0, _WAITING, 'Waiting for the cluster lock'),
        (300, _RUNNING, None),
        (400, _SUCCEEDED, None),
    ])

    await _collect_timed(clock, log_path)

    running_checks = [t for t in checks if 300 <= t < 400]
    # Seen within one capped interval, then checked every second.
    assert running_checks[0] <= 300 + _CAP + _TICK + _EPS
    assert max(_intervals(running_checks)) <= 1 + _TICK + _EPS


@pytest.mark.asyncio
async def test_new_log_bytes_end_the_backoff(monkeypatch, tmp_path, clock):
    """A resume writes to the log: the bytes, and a status check, follow.

    Status checks are backed off while parked, so it is the log that tells
    the tail first that the request runs again.
    """
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    checks = _status_timeline(monkeypatch, clock, [
        (0, _WAITING, 'Waiting for the cluster lock'),
        (300, _RUNNING, None),
        (400, _SUCCEEDED, None),
    ])

    def resume():
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('resumed\n')

    clock.at(300, resume)

    chunks = await _collect_timed(clock, log_path)

    delivered = [t for t, chunk in chunks if 'resumed' in chunk]
    # The quiet log is read about once a second while parked.
    assert delivered and delivered[0] <= 300 + 1 + _TICK + _EPS
    # The next check is due at once rather than after the backed-off interval.
    first_check_after = min(t for t in checks if t >= 300)
    assert first_check_after <= delivered[0] + _TICK + _EPS


@pytest.mark.asyncio
async def test_a_new_parked_message_is_pushed_within_the_cap(
        monkeypatch, tmp_path, clock):
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    _status_timeline(monkeypatch, clock, [
        (0, _WAITING, 'Pending (Queue: q, Position: 3)'),
        (200, _WAITING, 'Pending (Queue: q, Position: 1)'),
        (300, _SUCCEEDED, None),
    ])

    chunks = await _collect_timed(clock, log_path)

    pushed = [
        t for t, chunk in chunks
        if 'Position: 1' in chunk and _decoded_controls([chunk])
    ]
    assert pushed and 200 <= pushed[0] <= 200 + _CAP + 2 * _TICK + _EPS


@pytest.mark.asyncio
@pytest.mark.parametrize('should_retry', [False, True])
async def test_a_cancel_while_parked_ends_the_stream(monkeypatch, tmp_path,
                                                     clock, should_retry):
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    _status_timeline(monkeypatch,
                     clock, [
                         (0, _WAITING, 'Waiting for the cluster lock'),
                         (200, _CANCELLED, None),
                     ],
                     should_retry=should_retry)

    chunks = await _collect_timed(clock, log_path)

    end, last = chunks[-1]
    assert 200 <= end <= 200 + _CAP + _TICK + _EPS
    if should_retry:
        assert (rich_utils.Control.RETRY, '') in _decoded_controls([last])
    else:
        assert 'request rid cancelled' in last


@pytest.mark.asyncio
async def test_a_parked_request_that_vanishes_ends_the_stream(
        monkeypatch, tmp_path, clock):
    log_path = tmp_path / 'request.log'
    log_path.write_text('provisioning...\n')
    checks = _status_timeline(monkeypatch, clock, [
        (0, _WAITING, 'Waiting for the cluster lock'),
        (200, None, None),
    ])

    await _collect_timed(clock, log_path)

    assert 200 <= checks[-1] <= 200 + _CAP + _TICK + _EPS


class _RecordingLogProvider(log_provider.LocalLogProvider):
    """Records discards, in order, into a shared event list."""

    def __init__(self, events):
        self.events = events

    def discard_log(self, request_id: str) -> None:
        self.events.append(f'discarded {request_id}')


def _record_discards(monkeypatch, events):
    provider = _RecordingLogProvider(events)
    monkeypatch.setattr(log_provider, 'get_log_provider', lambda: provider)


async def _stream(events, *chunks):
    try:
        for chunk in chunks:
            yield chunk
    finally:
        events.append('closed')


@pytest.mark.asyncio
async def test_a_discarded_log_is_dropped_once_the_stream_ends(monkeypatch):
    events = []
    _record_discards(monkeypatch, events)

    chunks = [
        chunk async for chunk in stream_utils._discard_log_after_stream(
            _stream(events, 'hello\n'), 'rid')
    ]

    assert chunks == ['hello\n']
    assert events == ['closed', 'discarded rid']


@pytest.mark.asyncio
async def test_a_discarded_log_is_dropped_when_the_client_disconnects(
        monkeypatch):
    """A client that walks away mid-tail must not leave its copy behind.

    The stream is closed before the discard: an open fd keeps the log's
    blocks allocated after the unlink.
    """
    events = []
    _record_discards(monkeypatch, events)

    gen = stream_utils._discard_log_after_stream(
        _stream(events, 'hello\n', 'world\n'), 'rid')
    assert await gen.__anext__() == 'hello\n'
    await gen.aclose()

    assert events == ['closed', 'discarded rid']


def test_discard_log_removes_the_request_log(monkeypatch, tmp_path):
    monkeypatch.setattr(server_constants, 'REQUEST_LOG_PATH_PREFIX',
                        str(tmp_path))
    log_path = tmp_path / 'rid.log'
    log_path.write_text('hello\n')

    log_provider.LocalLogProvider().discard_log('rid')

    assert not log_path.exists()


@pytest.mark.asyncio
async def test_a_deleted_log_ends_the_stream_with_a_message(tmp_path):
    """The response has already started, so this cannot be a 404."""
    chunks = [
        chunk
        async for chunk in stream_utils.log_streamer(None, tmp_path / 'rid.log')
    ]

    assert len(chunks) == 1
    assert 'no longer available' in chunks[0]


# A line a task printed that happens to carry our own tags, with a body that
# is not JSON. Nothing stops a task from echoing these -- a test fixture, a
# log-processing tool, or a task that cats another SkyPilot log.
_PAYLOAD_SHAPED_LINE = ('epoch 3 <sky-payload type="x">'
                        '<rich_update>Waiting...</rich_update></sky-payload>')


def test_a_payload_shaped_task_line_is_not_a_payload():
    """`raise_for_mismatch=False` asks a question; it must answer, not raise.

    The body is unparseable, so the honest answer is "not ours", and the line
    must come back whole -- it is the task's own output.
    """
    is_payload, decoded = message_utils.decode_payload(_PAYLOAD_SHAPED_LINE,
                                                       raise_for_mismatch=False)

    assert is_payload is False
    assert decoded == _PAYLOAD_SHAPED_LINE


def test_a_strict_caller_still_rejects_an_unparseable_payload():
    """The other callers parse SkyPilot's own protocol output from remote
    commands, where an unparseable body IS an error. Only the classifying
    path was loosened.
    """
    with pytest.raises(json.JSONDecodeError):
        message_utils.decode_payload(_PAYLOAD_SHAPED_LINE)


@pytest.mark.asyncio
async def test_one_payload_shaped_line_does_not_truncate_the_stream(tmp_path):
    """The regression: it cut the log off, it did not just mangle a line.

    Classifying every line used to raise on this one, and the exception
    escaped the streaming generator -- so the response ended mid-body and
    everything after the line was silently lost. Downloads are where it bit:
    a tail of the last N lines usually skips past it, a whole-file read
    cannot.
    """
    log = tmp_path / 'rid.log'
    log.write_text('before\n' + _PAYLOAD_SHAPED_LINE + '\nafter\n')

    chunks = [
        chunk async for chunk in stream_utils.log_streamer(
            None, log, plain_logs=True, follow=False)
    ]
    streamed = ''.join(chunks)

    assert 'before' in streamed
    assert 'after' in streamed, 'the stream stopped at the payload-shaped line'
    assert _PAYLOAD_SHAPED_LINE in streamed, 'the task wrote it; show it'


@pytest.mark.parametrize('exc', [
    ValueError('Exceeds the limit (4300 digits) for integer string conversion'),
    RecursionError('maximum recursion depth exceeded'),
])
def test_no_parse_failure_escapes_the_classifier(monkeypatch, exc):
    """Bad syntax is not the only way `json.loads` fails.

    Deeply nested input exceeds the recursion limit, and since 3.11 an integer
    longer than `sys.get_int_max_str_digits()` raises a plain ValueError -- a
    long digit string is no less likely to come out of a task than a line of
    prose. Both thresholds are interpreter- and configuration-specific, so the
    failure is injected rather than provoked with a magic number: what is
    pinned is that NO parse failure escapes, not any one exception type.
    """

    def _boom(_):
        raise exc

    monkeypatch.setattr(json, 'loads', _boom)

    is_payload, decoded = message_utils.decode_payload(_PAYLOAD_SHAPED_LINE,
                                                       raise_for_mismatch=False)

    assert is_payload is False
    assert decoded == _PAYLOAD_SHAPED_LINE


@pytest.mark.parametrize('scalar', [123, None, True])
def test_a_scalar_payload_does_not_crash_the_decoder(scalar):
    """A task can print `<sky-payload>123</sky-payload>`.

    `Control.decode` does `in` on the decoded body: containers survive it,
    scalars raise TypeError -- and `decode_rich_status` has no except, while
    `read_provision_status_from_log` guards only OSError/ValueError, so the
    crash reached the client.
    """
    control, msg = rich_utils.Control.decode(scalar)

    assert control is None
    assert msg == scalar


def test_a_dict_payload_is_still_ours():
    """`instance_setup` sends `{'ray_port': N}`, and provision logs are full
    of them. Classifying those as task output would print the raw frame to the
    user instead of dropping it.
    """
    line = '<sky-payload>{"ray_port": 6380}</sky-payload>'

    is_payload, decoded = message_utils.decode_payload(line,
                                                       raise_for_mismatch=False)

    assert is_payload is True
    assert decoded == {'ray_port': 6380}
    assert rich_utils.Control.decode(decoded) == (None, {'ray_port': 6380})


def test_a_type_mismatch_returns_the_whole_line():
    """The loop variable used to shadow the parameter, so this returned the
    matched fragment rather than the line the task wrote."""
    line = 'noise <sky-payload type="x">{"a": 1}</sky-payload> tail'

    is_payload, decoded = message_utils.decode_payload(line,
                                                       payload_type='y',
                                                       raise_for_mismatch=False)

    assert is_payload is False
    assert decoded == line


@pytest.mark.asyncio
async def test_a_non_utf8_byte_does_not_lose_the_log(tmp_path):
    """A task writes bytes, not text -- one cat of a binary file is enough.

    A strict decode raised out of the generator before the buffer was ever
    flushed, so the whole log was lost, not just the offending line.
    """
    log = tmp_path / 'rid.log'
    log.write_bytes(b'before\n' + b'weird \xff byte\n' + b'after\n')

    for plain in (True, False):
        chunks = [
            chunk async for chunk in stream_utils.log_streamer(
                None, log, plain_logs=plain, follow=False)
        ]
        streamed = ''.join(chunks)

        assert 'before' in streamed, f'plain_logs={plain}'
        assert 'after' in streamed, f'plain_logs={plain}: the log was lost'


@pytest.mark.asyncio
async def test_a_failure_after_output_says_why_the_log_stopped(monkeypatch):
    """The boundary exists for the trigger nobody has thought of yet.

    Three have been found by being reported, each silent: the response ended
    mid-body with nothing logged. Whatever the fourth turns out to be, a
    reader who already has half a log should be told why it stopped.
    """

    async def _half_then_boom(*args, **kwargs):
        yield 'first half\n'
        raise RuntimeError('some future trigger')

    monkeypatch.setattr(stream_utils, '_log_stream_chunks', _half_then_boom)

    streamed = ''.join(
        [chunk async for chunk in stream_utils.log_streamer(None, None)])

    assert 'first half' in streamed
    assert 'Log streaming stopped' in streamed
    # The reason belongs in the server log, not in a response body: exception
    # text can carry a SQL statement or a server-side path.
    assert 'some future trigger' not in streamed
    assert 'RuntimeError' not in streamed


@pytest.mark.asyncio
async def test_a_failure_before_any_output_keeps_the_empty_signal(monkeypatch):
    """An empty response is a signal, not an absence.

    `sky jobs logs --sync-down` falls back to rsync on bytes_written == 0, so
    a marker line here would suppress the fallback and save a one-line log in
    place of the real one.

    It has to end CLEANLY, not raise: the SDK reads with `iter_content` and no
    except, so an aborted chunked response throws ChunkedEncodingError before
    that check is reached. `logger.exception` is what keeps the failure
    visible.
    """

    async def _boom(*args, **kwargs):
        raise RuntimeError('failed before the first chunk')
        yield  # pylint: disable=unreachable

    monkeypatch.setattr(stream_utils, '_log_stream_chunks', _boom)

    logged = []
    monkeypatch.setattr(stream_utils.logger, 'exception',
                        lambda msg, *a, **k: logged.append(msg))

    chunks = [chunk async for chunk in stream_utils.log_streamer(None, None)]

    assert not chunks, 'anything here suppresses the sync-down fallback'
    # Returning empty is only defensible because the failure is recorded.
    # Asserted on `logger.exception` itself rather than on captured output, so
    # quieting it to debug -- which would restore the silence this boundary
    # exists to remove -- fails here.
    assert logged, 'an empty response with no log entry is the old silence'


@pytest.mark.asyncio
async def test_a_404_is_still_a_404(monkeypatch):
    """`wait_for_request_to_start` raises 404 for an unknown request id, and
    the client is served that status. The error boundary must not turn it into
    an empty 200 -- it is control flow, not a streaming failure.
    """

    async def _not_found(*args, **kwargs):
        raise fastapi.HTTPException(status_code=404,
                                    detail='Request X not found')
        yield  # pylint: disable=unreachable

    monkeypatch.setattr(stream_utils, '_log_stream_chunks', _not_found)

    with pytest.raises(fastapi.HTTPException) as excinfo:
        async for _ in stream_utils.log_streamer('X', None):
            pass

    assert excinfo.value.status_code == 404


async def _collect_gzip(agen):
    return b''.join([chunk async for chunk in agen])


@pytest.mark.asyncio
async def test_a_failed_download_still_opens():
    """The artifact from the bug report: a `.log.gz` that will not open.

    The trailer is written on a natural EOF, so a stream that ended on an
    exception saved a gzip header and nothing else -- a file no tool reads,
    which hides even the part that did arrive.
    """

    async def _half_then_boom():
        yield 'first half\n'
        raise RuntimeError('boom mid-stream')

    with pytest.raises(RuntimeError):
        body = await _collect_gzip(stream_utils.gzip_stream(_half_then_boom()))

    # The bytes that did arrive are a complete gzip member, so the saved file
    # opens. Collected again because the raise above discards the partial.
    body = b''
    agen = stream_utils.gzip_stream(_half_then_boom())
    try:
        async for chunk in agen:
            body += chunk
    except RuntimeError:
        pass

    assert body, 'nothing was written at all'
    assert gzip.decompress(body).decode() == 'first half\n'


@pytest.mark.asyncio
async def test_an_empty_stream_stays_empty_through_gzip():
    """Not even a header: `bytes_written == 0` is the sync-down signal, and a
    10-byte gzip header would read as content and suppress it.
    """

    async def _nothing():
        return
        yield  # pylint: disable=unreachable

    assert await _collect_gzip(stream_utils.gzip_stream(_nothing())) == b''


@pytest.mark.asyncio
async def test_a_complete_stream_round_trips():
    """The control: the ordinary path must still produce a readable file."""

    async def _two_lines():
        yield 'one\n'
        yield 'two\n'

    body = await _collect_gzip(stream_utils.gzip_stream(_two_lines()))

    assert gzip.decompress(body).decode() == 'one\ntwo\n'

"""Streaming contracts shared by the requests and aiohttp log consumers."""
import asyncio
import types
from unittest import mock

import aiohttp
import pytest
import requests

from sky import exceptions
from sky.server import stream_utils
from sky.utils import message_utils
from sky.utils import rich_utils


# ------------------------- Transport test helpers ------------------------- #
def _chunks_with_errors(chunks):
    for chunk in chunks:
        if isinstance(chunk, BaseException):
            raise chunk
        yield chunk


async def _async_chunks(chunks):
    for chunk in _chunks_with_errors(chunks):
        yield chunk, False


def _response(mode, chunks):
    if mode == 'sync':
        response = mock.Mock(spec=requests.Response)
        response.iter_content.return_value = _chunks_with_errors(chunks)
    else:
        response = mock.Mock(spec=aiohttp.ClientResponse)
        response.content = mock.Mock()
        response.content.iter_chunks.return_value = _async_chunks(chunks)
    return response


async def _collect_async(response):
    return [
        line async for line in rich_utils.decode_rich_status_async(response)
    ]


def _decode(mode, chunks):
    response = _response(mode, chunks)
    if mode == 'sync':
        return list(rich_utils.decode_rich_status(response))
    return asyncio.run(_collect_async(response))


def _partitions(data):
    # Include empty chunks and every two-way split, especially offset zero.
    for offset in range(len(data) + 1):
        yield [b'', data[:offset], b'', data[offset:], b'']
    yield [bytes([byte]) for byte in data]


def _control(control, text=''):
    return message_utils.encode_payload(control.encode(text))


# ------------------------------------------------------------------------- #


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('text', ['é', '█', '😀', 'prefix é█😀'])
@pytest.mark.parametrize('ending', ['', '\n'])
def test_unicode_at_every_byte_boundary(mode, text, ending):
    expected = text + ending
    for chunks in _partitions(expected.encode('utf-8')):
        assert _decode(mode, chunks) == [expected], chunks


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('ending', ['', '\n'])
@pytest.mark.parametrize('data, expected', [
    (b'fatal: out of memory', 'fatal: out of memory'),
    (b'\xffbad', '�bad'),
    (b'\xe2(\xa1', '�(�'),
    (b'\xf0\x28\x8c\xbc', '�(��'),
    (b'\xe2\x96', '�'),
    (b'prefix \xf0\x9f', 'prefix �'),
    (b'\xff\xe2\x96', '��'),
])
def test_diagnostics_and_invalid_utf8(mode, ending, data, expected):
    for chunks in _partitions(data + ending.encode()):
        assert _decode(mode, chunks) == [expected + ending], chunks


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize(
    'chunks, expected',
    [
        ([], []),
        ([b'', b''], []),
        ([b'one\ntwo\nlast'], ['one\n', 'two\n', 'last']),
        ([b'10%\r20%\r100%\n'], ['10%\r', '20%\r', '100%\n']),
        ([b'10%\r', b'', b'20%\r', b'100%\n'], ['10%\r', '20%\r', '100%\n']),
        ([b'one\r\ntwo\r\n'], ['one\n', 'two\n']),
        # Preserve immediate CR progress output even when CRLF spans chunks.
        ([b'one\r', b'', b'\ntwo\r', b'\n'], ['one\r', '\n', 'two\r', '\n']),
        ([b'one\n\n', b'\n'], ['one\n', '\n', '\n']),
        ([b'unfinished', None, b'ignored\n'], []),
    ])
def test_line_framing(mode, chunks, expected):
    assert _decode(mode, chunks) == expected


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('ending', ['', '\n'])
def test_status_controls_and_cleanup(mode, ending):
    controls = [
        _control(rich_utils.Control.INIT, 'Launching'),
        _control(rich_utils.Control.START),
        _control(rich_utils.Control.UPDATE, 'Ready █'),
        _control(rich_utils.Control.STOP),
        _control(rich_utils.Control.HEARTBEAT),
        _control(rich_utils.Control.EXIT).rstrip('\n') + ending,
    ]
    data = ''.join(controls).encode()
    for chunks in _partitions(data):
        with mock.patch.object(rich_utils, 'client_status') as status_factory:
            assert _decode(mode, chunks) == []
        status_factory.assert_called_once_with('Launching')
        assert status_factory.return_value.mock_calls == [
            mock.call.start(),
            mock.call.update('Ready █'),
            mock.call.stop(),
            mock.call.__exit__(None, None, None),
            mock.call.__exit__(None, None, None),
        ]


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('control', list(rich_utils.Control))
def test_final_control_without_newline(mode, control):
    prefix = (b'' if control == rich_utils.Control.INIT else _control(
        rich_utils.Control.INIT, 'Launching').encode())
    data = _control(control, 'final').rstrip('\n').encode()
    for chunks in _partitions(data):
        with mock.patch.object(rich_utils, 'client_status') as status_factory:
            if control == rich_utils.Control.RETRY:
                with pytest.raises(exceptions.RequestInterruptedError):
                    _decode(mode, [prefix] + chunks)
            else:
                assert _decode(mode, [prefix] + chunks) == []
        status_factory.assert_called_once_with(
            'final' if control == rich_utils.Control.INIT else 'Launching')
        expected = {
            rich_utils.Control.START: [mock.call.start()],
            rich_utils.Control.UPDATE: [mock.call.update('final')],
            rich_utils.Control.STOP: [mock.call.stop()],
            rich_utils.Control.EXIT: [mock.call.__exit__(None, None, None)],
        }.get(control, []) + [mock.call.__exit__(None, None, None)]
        assert status_factory.return_value.mock_calls == expected


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('ending', ['', '\n'])
@pytest.mark.parametrize(
    'payload', [None, 1, True, {
        'key': 'value'
    }, ['value'], 'ordinary payload'])
def test_non_control_payloads_remain_hidden(mode, ending, payload):
    data = message_utils.encode_payload(payload).rstrip('\n') + ending
    for chunks in _partitions(data.encode()):
        assert _decode(mode, chunks) == []


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('ending', ['', '\n'])
def test_malformed_payload_remains_log_text(mode, ending):
    text = '<sky-payload>invalid json</sky-payload>' + ending
    for chunks in _partitions(text.encode()):
        assert _decode(mode, chunks) == [text]


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('ending', ['', '\n'])
def test_retry_still_raises_and_cleans_up(mode, ending):
    init = _control(rich_utils.Control.INIT, 'Launching').encode()
    retry = _control(rich_utils.Control.RETRY).rstrip('\n') + ending
    for chunks in _partitions(retry.encode()):
        with mock.patch.object(rich_utils, 'client_status') as status_factory:
            with pytest.raises(exceptions.RequestInterruptedError,
                               match='Streaming interrupted. Please retry.'):
                _decode(mode, [init] + chunks)
        status_factory.return_value.__exit__.assert_called_once_with(
            None, None, None)


@pytest.mark.parametrize('ending', ['', '\n'])
def test_relay_controls_and_heartbeat(ending):
    init = _control(rich_utils.Control.INIT, 'Launching')
    update = _control(rich_utils.Control.UPDATE, 'Ready')
    heartbeat = _control(rich_utils.Control.HEARTBEAT).rstrip('\n') + ending
    data = (init + update + 'log\n' + heartbeat).encode()
    for chunks in _partitions(data):
        with mock.patch.object(rich_utils, 'client_status') as status_factory:
            result = list(
                rich_utils.decode_rich_status(_response('sync', chunks),
                                              relay_rich_status=True))
        assert result == [init, update, 'log\n', None]
        status_factory.assert_not_called()


@pytest.mark.parametrize('ending', ['', '\n'])
def test_relay_retry_still_raises(ending):
    retry = _control(rich_utils.Control.RETRY).rstrip('\n') + ending
    for chunks in _partitions(retry.encode()):
        with pytest.raises(exceptions.RequestInterruptedError):
            list(
                rich_utils.decode_rich_status(_response('sync', chunks),
                                              relay_rich_status=True))


def test_relay_final_status_without_newline():
    line = _control(rich_utils.Control.INIT, 'Launching').rstrip('\n')
    for chunks in _partitions(line.encode()):
        assert list(
            rich_utils.decode_rich_status(_response('sync', chunks),
                                          relay_rich_status=True)) == [line]


@pytest.mark.parametrize('mode', ['sync', 'async'])
def test_transport_failure_does_not_flush_or_hide_error(mode):
    error = (requests.exceptions.ChunkedEncodingError('stream broke')
             if mode == 'sync' else aiohttp.ClientPayloadError('stream broke'))
    chunks = [
        _control(rich_utils.Control.INIT, 'Launching').encode(),
        b'visible\nnot complete \xe2', error
    ]
    seen = []
    response = _response(mode, chunks)

    async def consume():
        async for line in rich_utils.decode_rich_status_async(response):
            seen.append(line)

    with mock.patch.object(rich_utils, 'client_status') as status_factory:
        with pytest.raises(type(error), match='stream broke') as raised:
            if mode == 'sync':
                seen.extend(rich_utils.decode_rich_status(response))
            else:
                asyncio.run(consume())
    assert raised.value is error
    assert seen == ['visible\n']
    status_factory.return_value.__exit__.assert_called_once_with(
        None, None, None)


@pytest.mark.parametrize('mode', ['sync', 'async'])
def test_lazy_consumption_and_early_close(mode):
    consumed = []

    def chunks():
        yield _control(rich_utils.Control.INIT, 'Launching').encode()
        consumed.append('first')
        yield b'first\n'
        consumed.append('second')
        yield b'second\n'

    async def consume():
        stream = rich_utils.decode_rich_status_async(_response(mode, chunks()))
        assert not consumed
        assert await stream.__anext__() == 'first\n'
        assert consumed == ['first']
        assert isinstance(stream, types.AsyncGeneratorType)
        await stream.aclose()

    with mock.patch.object(rich_utils, 'client_status') as status_factory:
        if mode == 'sync':
            stream = rich_utils.decode_rich_status(_response(mode, chunks()))
            assert not consumed
            assert next(stream) == 'first\n'
            assert consumed == ['first']
            assert isinstance(stream, types.GeneratorType)
            stream.close()
        else:
            asyncio.run(consume())
    assert consumed == ['first']
    status_factory.return_value.__exit__.assert_called_once_with(
        None, None, None)


def test_async_cancellation_cleans_up_without_flushing():
    chunks = [
        _control(rich_utils.Control.INIT, 'Launching').encode(), b'incomplete',
        asyncio.CancelledError()
    ]
    with mock.patch.object(rich_utils, 'client_status') as status_factory:
        with pytest.raises(asyncio.CancelledError):
            _decode('async', chunks)
    status_factory.return_value.__exit__.assert_called_once_with(
        None, None, None)


@pytest.mark.parametrize('mode', ['sync', 'async'])
@pytest.mark.parametrize('ending', ['', '\n'])
def test_server_file_tail_to_client(mode, ending, tmp_path):
    """Pin the actual server flush contract without an HTTP server or DB."""
    diagnostic = 'fatal: out of memory █' + ending
    path = tmp_path / 'request.log'
    path.write_text('earlier log\n' + diagnostic, encoding='utf-8')

    async def tail():
        # pylint: disable=protected-access
        return [
            chunk
            async for chunk in stream_utils._stream_log_file(path, follow=False)
        ]

    server_chunks = asyncio.run(tail())
    assert ''.join(server_chunks) == 'earlier log\n' + diagnostic
    data = ''.join(server_chunks).encode()
    for chunks in _partitions(data):
        assert _decode(mode, chunks) == ['earlier log\n', diagnostic]

"""Unit tests for sky.server.stream_utils."""

import errno
from unittest import mock

import pytest

from sky.server.stream_utils import _tail_log_file


class FakeAsyncFile:
    """A fake async file object that yields predetermined chunks."""

    def __init__(self, chunks):
        """Args:
            chunks: list of bytes or OSError exceptions. Each call to read()
                returns the next item, or raises it if it's an exception.
        """
        self._chunks = list(chunks)
        self._index = 0

    async def read(self, size=-1):  # pylint: disable=unused-argument
        if self._index >= len(self._chunks):
            return b''
        item = self._chunks[self._index]
        self._index += 1
        if isinstance(item, Exception):
            raise item
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        item = self._chunks[self._index]
        self._index += 1
        if isinstance(item, Exception):
            raise item
        return item


async def _collect(async_gen):
    """Collect all items from an async generator into a list."""
    result = []
    async for item in async_gen:
        result.append(item)
    return result


@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock out DB-dependent modules so _tail_log_file can run standalone."""
    with mock.patch(
            'sky.server.stream_utils.requests_lib') as mock_requests_lib:
        mock_status = mock.AsyncMock()
        mock_status.status = 999  # Greater than RUNNING to trigger break
        mock_requests_lib.get_request_status_async.return_value = mock_status
        mock_requests_lib.RequestStatus.RUNNING = 100
        mock_requests_lib.RequestStatus.CANCELLED = 200
        yield mock_requests_lib


@pytest.mark.asyncio
async def test_tail_log_file_stale_handle_during_read():
    """OSError during chunked read breaks the loop and flushes the buffer."""

    stale_error = OSError(errno.ESTALE, 'Stale file handle')
    fake_file = FakeAsyncFile([
        b'line one\nline two\n',
        stale_error,
    ])

    chunks = await _collect(
        _tail_log_file(fake_file, request_id=None, follow=False))

    combined = ''.join(chunks)
    assert 'line one\n' in combined
    assert 'line two\n' in combined


@pytest.mark.asyncio
async def test_tail_log_file_stale_handle_during_tail():
    """OSError during tail-reading yields lines collected before the error."""

    stale_error = OSError(errno.ESTALE, 'Stale file handle')
    # __aiter__/__anext__ path: two valid lines then an error
    fake_file = FakeAsyncFile([
        b'first line\n',
        b'second line\n',
        stale_error,
    ])

    chunks = await _collect(
        _tail_log_file(fake_file, request_id=None, tail=10, follow=False))

    combined = ''.join(chunks)
    assert 'first line\n' in combined
    assert 'second line\n' in combined


@pytest.mark.asyncio
async def test_tail_log_file_stale_handle_on_first_read():
    """OSError on the very first read returns empty output gracefully."""

    stale_error = OSError(errno.ESTALE, 'Stale file handle')
    fake_file = FakeAsyncFile([stale_error])

    chunks = await _collect(
        _tail_log_file(fake_file, request_id=None, follow=False))

    # Should not raise; output may be empty
    combined = ''.join(chunks)
    assert isinstance(combined, str)


@pytest.mark.asyncio
async def test_tail_log_file_normal_read_no_error():
    """Normal operation without errors still works correctly."""

    fake_file = FakeAsyncFile([
        b'hello world\n',
        b'',  # EOF
    ])

    chunks = await _collect(
        _tail_log_file(fake_file, request_id=None, follow=False))

    combined = ''.join(chunks)
    assert 'hello world\n' in combined

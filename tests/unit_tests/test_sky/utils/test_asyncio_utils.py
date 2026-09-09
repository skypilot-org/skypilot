"""Unit tests for sky.utils.asyncio_utils.NonOwningPipeReader.

The reader watches a pipe fd through `loop.add_reader()` and must never close
the fd itself. That property is what keeps the API server's kubectl stdout
pipe from being closed twice under uvloop (one close by libuv, one by the
`Popen.stdout` file object), which frees the fd number for another thread and
then closes it under that thread. The tests run on asyncio and, when
installed, on uvloop.
"""

import asyncio
import gc
import io
import os
from typing import Callable, List

import pytest

from sky.utils import asyncio_utils


def _loop_factories() -> List[Callable[[], asyncio.AbstractEventLoop]]:
    factories: List[Callable[[], asyncio.AbstractEventLoop]] = [
        asyncio.new_event_loop
    ]
    try:
        import uvloop  # pylint: disable=import-outside-toplevel
        factories.append(uvloop.new_event_loop)
    except ImportError:
        pass
    return factories


def _loop_id(factory: Callable[[], asyncio.AbstractEventLoop]) -> str:
    return factory.__module__.split('.')[0]


@pytest.fixture(params=_loop_factories(), ids=_loop_id)
def loop(request):
    the_loop = request.param()
    asyncio.set_event_loop(the_loop)
    try:
        yield the_loop
    finally:
        the_loop.run_until_complete(asyncio.sleep(0))
        the_loop.close()
        asyncio.set_event_loop(None)


def _fd_is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


async def _spin(loop: asyncio.AbstractEventLoop, iterations: int = 20):
    del loop  # unused
    for _ in range(iterations):
        await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0.01)


def test_reads_lines_without_owning_fd(loop):
    """readline() works and stop() leaves the fd open for its owner."""
    read_fd, write_fd = os.pipe()
    # Mirror subprocess.Popen.stdout: a BufferedReader that owns read_fd.
    owner = io.open(read_fd, 'rb', closefd=True)
    ident = os.fstat(read_fd)

    async def scenario():
        reader = asyncio_utils.NonOwningPipeReader(loop, owner.fileno())
        reader.start()
        os.write(
            write_fd, b'Handling connection for 1\n'
            b'Forwarding from 127.0.0.1:4242 -> 22\n')
        assert await reader.readline() == b'Handling connection for 1\n'
        assert (await
                reader.readline()) == b'Forwarding from 127.0.0.1:4242 -> 22\n'
        reader.stop()
        reader.stop()  # idempotent
        await _spin(loop)

    loop.run_until_complete(scenario())
    # The reader did not close the fd: same open file, same inode.
    assert _fd_is_open(read_fd)
    after = os.fstat(read_fd)
    assert (after.st_dev, after.st_ino) == (ident.st_dev, ident.st_ino)
    # The owner closes it exactly once.
    owner.close()
    assert not _fd_is_open(read_fd)
    os.close(write_fd)


def test_eof_stops_watching_and_owner_closes_once(loop):
    """EOF unregisters the fd; the closed number is safe to reuse."""
    read_fd, write_fd = os.pipe()
    owner = io.open(read_fd, 'rb', closefd=True)

    async def scenario():
        reader = asyncio_utils.NonOwningPipeReader(loop, owner.fileno())
        reader.start()
        os.write(write_fd, b'Error from server (NotFound)\n')
        os.close(write_fd)
        assert await reader.readline() == b'Error from server (NotFound)\n'
        assert await reader.readline() == b''  # EOF
        # Still open: EOF only stopped the watch.
        assert _fd_is_open(read_fd)
        # remove_reader on an fd that EOF already unregistered must not raise
        # on either loop (the handler's `finally` calls stop() again).
        reader.stop()
        loop.remove_reader(read_fd)
        owner.close()
        assert not _fd_is_open(read_fd)

        # fd-reuse canary: the next pipe normally takes the lowest free
        # number, i.e. the one just closed (another thread may grab it first,
        # so this is not asserted). Nothing may close the canary behind our
        # back once the loop runs its pending callbacks and the GC collects
        # the reader.
        canary_r, canary_w = os.pipe()
        del reader
        await _spin(loop)
        os.write(canary_w, b'x')
        assert os.read(canary_r, 1) == b'x'
        os.close(canary_r)
        os.close(canary_w)

    loop.run_until_complete(scenario())


def test_drain_is_non_blocking(loop):
    read_fd, write_fd = os.pipe()
    owner = io.open(read_fd, 'rb', closefd=True)

    async def scenario():
        reader = asyncio_utils.NonOwningPipeReader(loop, owner.fileno())
        reader.start()
        os.write(
            write_fd, b'Forwarding from 127.0.0.1:4242 -> 22\n'
            b'Forwarding from [::1]:4242 -> 22\n')
        assert (await
                reader.readline()) == b'Forwarding from 127.0.0.1:4242 -> 22\n'
        reader.stop()
        # Writer still alive, pipe possibly empty: must return immediately.
        os.write(write_fd, b'E0908 lost connection to pod\n')
        assert reader.drain() == b'E0908 lost connection to pod\n'
        assert reader.drain() == b''
        os.close(write_fd)
        assert reader.drain() == b''
        assert _fd_is_open(read_fd)

    loop.run_until_complete(scenario())
    owner.close()


def test_drain_caps_bytes(loop):
    read_fd, write_fd = os.pipe()
    owner = io.open(read_fd, 'rb', closefd=True)
    reader = asyncio_utils.NonOwningPipeReader(loop, owner.fileno())
    os.set_blocking(read_fd, False)
    os.write(write_fd, b'x' * 1000)
    assert len(reader.drain(max_bytes=100)) == 100
    assert len(reader.drain()) == 900
    os.close(write_fd)
    owner.close()

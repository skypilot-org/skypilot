"""Unit tests for the subprocess plumbing in sky.utils.asyncio_utils.

Two properties, both only observable on a real event loop, and both about not
letting the loop take ownership of something it will mishandle:

* `NonOwningPipeReader` / `NonOwningPipeWriter` watch a pipe fd through
  `loop.add_reader()` / `add_writer()` and must never close the fd. That is
  what keeps the API server's kubectl stdout pipe from being closed twice
  under uvloop (one close by libuv, one by the `Popen.stdout` file object),
  which frees the fd number for another thread and then closes it under that
  thread.
* `spawn_without_fork` must reach execve without forking. Under uvloop
  `asyncio.create_subprocess_exec` goes through libuv's `uv_spawn`, which
  forks and blocks the parent -- GIL held, so the whole process -- until the
  child execs.

The tests run on asyncio and, when installed, on uvloop.
"""

import asyncio
import gc
import io
import os
import subprocess
import sys
import threading
import time
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


# Stands in for a long-running child: announces itself, then waits to be
# killed. Writing to fd 1 directly keeps stdio buffering out of the picture.
_STAY_UP = ('import os, time\n'
            'os.write(1, b"up\\n")\n'
            'time.sleep(3600)\n')


def _cmd(tmp_path, body: str, name: str = 'child.py') -> List[str]:
    script = tmp_path / name
    script.write_text(body)
    return [sys.executable, str(script)]


def test_writer_writes_without_owning_fd(loop):
    """write() delivers bytes and close() leaves the fd open for its owner."""
    read_fd, write_fd = os.pipe()
    # Mirror subprocess.Popen.stdin: a BufferedWriter that owns write_fd.
    owner = io.open(write_fd, 'wb', closefd=True)
    ident = os.fstat(write_fd)

    async def scenario():
        writer = asyncio_utils.NonOwningPipeWriter(loop, owner.fileno())
        writer.start()
        await writer.write(b'hello ')
        await writer.write(b'world\n')
        assert os.read(read_fd, 64) == b'hello world\n'
        writer.close()
        writer.close()  # idempotent
        await _spin(loop)

    loop.run_until_complete(scenario())
    assert _fd_is_open(write_fd)
    after = os.fstat(write_fd)
    assert (after.st_dev, after.st_ino) == (ident.st_dev, ident.st_ino)
    owner.close()
    assert not _fd_is_open(write_fd)
    os.close(read_fd)


def test_writer_waits_out_a_full_pipe(loop):
    """A write larger than the pipe buffer completes without blocking."""
    read_fd, write_fd = os.pipe()
    owner = io.open(write_fd, 'wb', closefd=True)
    payload = b'x' * (1 << 20)  # far beyond any pipe buffer
    drained = bytearray()

    async def drain_slowly():
        # Start behind on purpose so the writer has to park on add_writer().
        await asyncio.sleep(0.05)
        os.set_blocking(read_fd, False)
        while len(drained) < len(payload):
            try:
                chunk = os.read(read_fd, 65536)
            except BlockingIOError:
                await asyncio.sleep(0)
                continue
            if not chunk:
                break
            drained.extend(chunk)

    async def scenario():
        writer = asyncio_utils.NonOwningPipeWriter(loop, owner.fileno())
        writer.start()
        await asyncio.gather(writer.write(payload), drain_slowly())
        writer.close()

    loop.run_until_complete(asyncio.wait_for(scenario(), 30))
    assert bytes(drained) == payload
    owner.close()
    os.close(read_fd)


def test_reader_read_returns_chunks(loop):
    read_fd, write_fd = os.pipe()
    owner = io.open(read_fd, 'rb', closefd=True)

    async def scenario():
        reader = asyncio_utils.NonOwningPipeReader(loop, owner.fileno())
        reader.start()
        os.write(write_fd, b'abcdef')
        assert await reader.read(3) == b'abc'
        os.close(write_fd)
        assert await reader.read(64) == b'def'
        assert await reader.read(64) == b''  # EOF
        reader.stop()

    loop.run_until_complete(scenario())
    owner.close()


@pytest.mark.skipif(
    not getattr(subprocess, '_USE_POSIX_SPAWN', False),
    reason='CPython does not use posix_spawn() on this platform, so there is '
    'no fork-free path to assert on')
def test_spawn_without_fork_does_not_fork(loop, tmp_path):
    """The spawn must reach execve without forking this process.

    `os.register_at_fork` handlers run on every fork CPython knows about, and
    uvloop runs them around `uv_spawn`. posix_spawn() runs none of them.
    """
    forked: List[int] = []
    # Handlers cannot be unregistered; keep it to a list append.
    os.register_at_fork(after_in_parent=lambda: forked.append(1))

    async def scenario():
        proc = await asyncio_utils.spawn_without_fork(_cmd(tmp_path, _STAY_UP))
        try:
            assert proc.stdout is not None
            reader = asyncio_utils.NonOwningPipeReader(loop,
                                                       proc.stdout.fileno())
            reader.start()
            assert await reader.readline() == b'up\n'
            reader.stop()
        finally:
            proc.kill()
            proc.wait()
            assert proc.stdout is not None
            proc.stdout.close()

    loop.run_until_complete(scenario())
    assert not forked, (
        'spawn_without_fork forked the parent. On a serving loop that freezes '
        'the whole process for as long as the fork takes; keep the '
        'posix_spawn() arguments intact.')


def test_spawn_without_fork_passes_posix_spawn_arguments(
        loop, tmp_path, monkeypatch):
    """Pin the Popen arguments CPython's posix_spawn() fast path requires."""
    captured = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', spy)

    async def scenario():
        proc = await asyncio_utils.spawn_without_fork(_cmd(tmp_path, _STAY_UP))
        proc.kill()
        proc.wait()
        assert proc.stdout is not None
        proc.stdout.close()

    loop.run_until_complete(scenario())

    assert captured['close_fds'] is False
    assert os.path.isabs(captured['executable'])
    for disqualifying in ('preexec_fn', 'pass_fds', 'cwd', 'start_new_session',
                          'uid', 'gid', 'umask'):
        assert captured.get(disqualifying) is None, (
            f'{disqualifying} disqualifies the posix_spawn() fast path; the '
            'spawn would silently revert to fork()')


def test_spawn_without_fork_rejects_unresolvable_argv0(loop):

    async def scenario():
        with pytest.raises(RuntimeError, match='not on PATH'):
            await asyncio_utils.spawn_without_fork(['definitely-not-a-binary'])

    loop.run_until_complete(scenario())


def test_cancelled_spawn_does_not_orphan_the_process(loop, tmp_path,
                                                     monkeypatch):
    """A cancel landing inside the spawn must not leave an unowned process.

    A thread already inside `Popen` cannot be cancelled, so the spawn still
    hands back a live process after the caller is gone; nothing else would
    ever find it.
    """
    spawned: List[subprocess.Popen] = []
    entered = threading.Event()
    real_popen = subprocess.Popen

    def slow_popen(*args, **kwargs):
        entered.set()
        # Hold the executor thread open so the cancel lands mid-spawn.
        time.sleep(0.5)
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(subprocess, 'Popen', slow_popen)

    async def scenario():
        task = asyncio.ensure_future(
            asyncio_utils.spawn_without_fork(_cmd(tmp_path, _STAY_UP)))
        assert await asyncio.get_event_loop().run_in_executor(
            None, entered.wait, 10), 'spawn never started'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if spawned and spawned[0].poll() is not None:
                break
            await asyncio.sleep(0.05)

    loop.run_until_complete(scenario())
    assert spawned, 'the executor never finished the spawn'
    assert spawned[0].poll() is not None, (
        'a cancelled spawn left a process running with no owner')

"""Asyncio utilities."""

import asyncio
import functools
import os
import random
import shutil
import subprocess
from typing import List, Optional, Set

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

_background_tasks: Set[asyncio.Task] = set()

# Upper bound on the random delay applied before a periodic housekeeping
# daemon runs its first pass. See sleep_startup_jitter().
DEFAULT_STARTUP_JITTER_SECONDS = 1800


async def sleep_startup_jitter(
        name: str, max_seconds: float = DEFAULT_STARTUP_JITTER_SECONDS) -> None:
    """Sleep a random offset before a periodic daemon's first pass.

    Periodic housekeeping daemons are written as::

        while True:
            do_the_pass()
            await asyncio.sleep(interval)

    so the first pass runs at t=0 of the process. That is fine for a single
    server, but it means every API server that boots at the same moment also
    starts its housekeeping at the same moment, and the passes stay aligned
    from then on: a fleet that is restarted together (a rolling upgrade, a
    node drain, an eviction) has its daily retention sweeps permanently
    phase-locked, so they all land on the shared database at once.

    Sleeping a random offset in [0, max_seconds) before entering the loop
    spreads those first passes out, and because the interval is unchanged the
    offset persists for the lifetime of the process.

    Only safe for work that is pure housekeeping -- nothing whose result is
    needed for the server to start serving correctly.
    """
    if max_seconds <= 0:
        return
    delay = random.uniform(0, max_seconds)
    logger.debug(
        '%s: delaying first pass by %.0fs to avoid a synchronized '
        'startup burst across servers', name, delay)
    await asyncio.sleep(delay)


def shield(func):
    """Shield the decorated async function from cancellation.

    If the outer coroutine is cancelled, the inner decorated function
    will be protected from cancellation by asyncio.shield(). And we will
    maintain a reference to the the inner task to avoid it get GCed before
    it is done.

    For example, filelock.AsyncFileLock is not cancellation safe. The
    following code:

        async def fn_with_lock():
            async with filelock.AsyncFileLock('lock'):
                await asyncio.sleep(1)

    is equivalent to:

        # The lock may leak if the cancellation happens in
        # lock.acquire() or lock.release()
        async def fn_with_lock():
            lock = filelock.AsyncFileLock('lock')
            await lock.acquire()
            try:
                await asyncio.sleep(1)
            finally:
                await lock.release()

    Shilding the function ensures there is no cancellation will happen in the
    function, thus the lock will be released properly:

        @shield
        async def fn_with_lock()

    Note that the resource acquisition and release should usually be protected
    in one @shield block but not separately, e.g.:

        lock = filelock.AsyncFileLock('lock')

        @shield
        async def acquire():
            await lock.acquire()

        @shield
        async def release():
            await lock.release()

        async def fn_with_lock():
            await acquire()
            try:
                do_something()
            finally:
                await release()

    The above code is not safe because if `fn_with_lock` is cancelled,
    `acquire()` and `release()` will be executed in the background
    concurrently and causes race conditions.
    """

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        task = asyncio.create_task(func(*args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            _background_tasks.add(task)
            task.add_done_callback(lambda _: _background_tasks.discard(task))
            raise

    return async_wrapper


# Bytes read from the pipe per readiness callback in NonOwningPipeReader.
_PIPE_READ_CHUNK = 65536


class NonOwningPipeReader:
    """Line reader for a pipe fd that never takes ownership of the fd.

    Feeds an `asyncio.StreamReader` from `loop.add_reader()` readiness
    callbacks. The caller keeps owning the fd (typically through
    `subprocess.Popen.stdout`) and must close it exactly once, after `stop()`.

    Why not `loop.connect_read_pipe(protocol_factory, pipe)`: it hands the
    loop a file object that owns the fd. asyncio's pipe transport closes that
    object once. uvloop's pipe transport closes the fd number twice: once
    through libuv (`uv_close`) and once through `pipe.close()`; whichever
    runs second gets EBADF, which is swallowed. The order depends on whether
    the transport is closed explicitly or torn down by the cyclic GC. CPython
    releases the GIL around its close(), so an fd allocated by another thread
    in that window takes the freed number and is then closed under its owner:
    a DB socket, a /proc file, another pipe. `add_reader()` /
    `remove_reader()` never close the fd on either loop (asyncio selectors;
    libuv `uv_poll`), so with this class the fd has exactly one owner.

    `start()` switches the fd to non-blocking mode. Do not read it through the
    owning file object while the reader is active.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, fd: int):
        self._loop = loop
        self._fd = fd
        self._reader = asyncio.StreamReader(loop=loop)
        self._watching = False

    @property
    def fd(self) -> int:
        return self._fd

    def start(self) -> None:
        """Start watching the fd for readable data."""
        os.set_blocking(self._fd, False)
        self._loop.add_reader(self._fd, self._on_readable)
        self._watching = True

    def stop(self) -> None:
        """Stop watching the fd. Idempotent.

        Must be called before the owner closes the fd: closing a watched fd
        leaves the loop with a watcher for a number the next pipe or socket
        can reuse.
        """
        if self._watching:
            self._watching = False
            self._loop.remove_reader(self._fd)

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, _PIPE_READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            self.stop()
            self._reader.set_exception(e)
            return
        if data:
            self._reader.feed_data(data)
        else:
            self.stop()
            self._reader.feed_eof()

    async def readline(self) -> bytes:
        """Read one line. Returns b'' at EOF (the writer closed the pipe)."""
        return await self._reader.readline()

    async def read(self, n: int = -1) -> bytes:
        """Read up to `n` bytes. Returns b'' at EOF."""
        return await self._reader.read(n)

    def drain(self, max_bytes: int = _PIPE_READ_CHUNK) -> bytes:
        """Return what the pipe holds right now, without blocking.

        Stops watching first. Meant for logging leftover output after the
        writer has exited; bytes already consumed by `readline()` are not
        included.
        """
        self.stop()
        chunks = []
        total = 0
        while total < max_bytes:
            try:
                data = os.read(self._fd, min(_PIPE_READ_CHUNK,
                                             max_bytes - total))
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            total += len(data)
        return b''.join(chunks)


class NonOwningPipeWriter:
    """Writer for a pipe fd that never takes ownership of the fd.

    The counterpart to `NonOwningPipeReader`, for the same reason:
    `loop.connect_write_pipe()` hands the loop a file object that owns the fd,
    and under uvloop the transport then closes that fd number twice. The
    caller keeps owning the fd (typically through `subprocess.Popen.stdin`)
    and must close it exactly once, after `close()`.

    `start()` switches the fd to non-blocking mode. Do not write through the
    owning file object while this writer is active, and drive it from one task
    at a time -- concurrent `write()` calls would interleave their bytes.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, fd: int):
        self._loop = loop
        self._fd = fd
        self._waiter: Optional[asyncio.Future] = None
        self._watching = False

    @property
    def fd(self) -> int:
        return self._fd

    def start(self) -> None:
        os.set_blocking(self._fd, False)

    async def write(self, data: bytes) -> None:
        """Write all of `data`, waiting for room in the pipe as needed."""
        view = memoryview(data)
        while view:
            try:
                sent = os.write(self._fd, view)
            except (BlockingIOError, InterruptedError):
                await self._wait_writable()
                continue
            view = view[sent:]

    async def _wait_writable(self) -> None:
        waiter = self._loop.create_future()
        self._waiter = waiter
        self._loop.add_writer(self._fd, self._on_writable)
        self._watching = True
        try:
            await waiter
        finally:
            self.close()

    def _on_writable(self) -> None:
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    def close(self) -> None:
        """Stop watching the fd. Idempotent.

        Must be called before the owner closes the fd: a watcher left on a
        closed number fires for whatever the kernel hands out next.
        """
        if self._watching:
            self._watching = False
            self._loop.remove_writer(self._fd)
        self._waiter = None


@functools.lru_cache(maxsize=1)
def _warn_once_if_spawn_still_forks() -> None:
    """Warns when CPython cannot give us a fork-free spawn on this platform.

    `subprocess` routes through `posix_spawn()` only where it considers it
    safe (on Linux, glibc >= 2.24). Everywhere else the arguments below are
    honoured but the spawn is still a fork, so the caller does not get what
    this module's name promises and the stall is back. Not fatal -- a server
    on such a platform should still run -- but it must not be silent.
    """
    if getattr(subprocess, '_USE_POSIX_SPAWN', False):
        return
    logger.warning(
        'CPython will not use posix_spawn() on this platform, so subprocess '
        'spawns fall back to fork(). On an event loop that serves requests, '
        'each spawn then blocks the whole process for as long as the fork '
        'takes.')


def _resolve_executable(argv0: str) -> str:
    """Absolute path for argv[0]; required for the posix_spawn() fast path."""
    if os.path.isabs(argv0):
        return argv0
    resolved = shutil.which(argv0)
    if resolved is None or not os.path.isabs(resolved):
        raise RuntimeError(
            f'{argv0!r} is not on PATH as an absolute path; refusing to fall '
            'back to a fork-based spawn, which blocks the whole process for '
            'as long as the fork takes.')
    return resolved


def _kill_orphan(loop: asyncio.AbstractEventLoop,
                 spawn: 'asyncio.Future') -> None:
    """Kill a process whose caller was cancelled before it took ownership.

    Runs on the loop, so it must not block: SIGKILL rather than SIGTERM, and
    the reap goes to a thread.
    """
    if spawn.cancelled() or spawn.exception() is not None:
        return
    proc = spawn.result()
    logger.warning(
        'Subprocess PID %d was spawned after its caller was cancelled; '
        'killing it.', proc.pid)
    try:
        proc.kill()
    except OSError:
        pass
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()
    loop.run_in_executor(None, proc.wait)


async def spawn_without_fork(
    argv: List[str],
    *,
    stdin: int = subprocess.DEVNULL,
    stdout: int = subprocess.PIPE,
    stderr: int = subprocess.STDOUT,
) -> subprocess.Popen:
    """Spawn a subprocess without forking this process.

    `asyncio.create_subprocess_exec` / `_shell` are not usable on a serving
    event loop. Under uvloop they go through libuv's `uv_spawn`, which forks
    and then blocks the parent until the child reaches `execve` -- with the
    GIL held, so the whole process stops, not just the loop -- while the
    forked child tears down the inherited Python heap on the way there. The
    cost scales with the parent's heap rather than with anything about the
    command, and on a large server it runs to seconds per spawn.

    `subprocess.Popen` with an absolute `executable` and `close_fds=False`
    takes CPython's `posix_spawn()` path instead, which never copies the
    address space and never runs Python in a forked child.

    The caller owns the returned process and its pipes, and is responsible for
    reaping it: `subprocess.Popen` is outside asyncio's child watcher, so
    `proc.wait()` has to be driven explicitly (from a thread, to keep it off
    the loop). Read its pipes with `NonOwningPipeReader` rather than handing
    their fds to the loop.

    Raises RuntimeError if argv[0] cannot be resolved to an absolute path,
    rather than silently falling back to a forking spawn.
    """
    _warn_once_if_spawn_still_forks()
    executable = _resolve_executable(argv[0])
    args = [executable] + list(argv[1:])

    def spawn_sync() -> subprocess.Popen:
        # Every argument here is load-bearing for CPython's posix_spawn() fast
        # path: an absolute `executable`, `close_fds=False`, and no
        # `preexec_fn` / `pass_fds` / `cwd` / `start_new_session` / uid / gid /
        # umask. Drop any one of them and this silently reverts to fork(),
        # which is what this function exists to avoid. Python opens its own
        # fds with O_CLOEXEC (PEP 446), so `close_fds=False` passes on stdio
        # only.
        return subprocess.Popen(
            args,
            executable=executable,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            close_fds=False,
        )

    loop = asyncio.get_running_loop()
    spawn = loop.run_in_executor(None, spawn_sync)
    try:
        return await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # A thread that has already entered Popen cannot be cancelled, so the
        # spawn still hands back a live process after this coroutine is gone.
        # Shield keeps that future alive and the callback owns whatever it
        # returns; otherwise the process would run with no owner, holding
        # whatever it had opened, until the server exits.
        spawn.add_done_callback(functools.partial(_kill_orphan, loop))
        raise

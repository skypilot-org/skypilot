"""Tests for the cancel signal files of managed jobs.

The signal directory can be on a network filesystem shared by API server
replicas, where a call can take seconds and a listing costs more the more
entries there are. These tests pin down that:

  - writing or taking a signal leaves no file behind;
  - a signal is taken whole or left in place, without a lock, also next to an
    older writer that still writes under one;
  - a controller process calls into the directory neither on its event loop
    nor in the thread pool it shares with its jobs;
  - the files that older code left behind are cleaned up, and nothing else.
"""
import asyncio
import concurrent.futures
import io
import os
import threading
import time
from typing import List
from unittest import mock

import filelock
import pytest

from sky.jobs import cancel_signal
from sky.jobs import constants as jobs_constants
from sky.jobs import controller as controller_module
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils

_OLD = cancel_signal._TEMPORARY_FILE_MAX_AGE_SECONDS + 600  # pylint: disable=protected-access


@pytest.fixture
def signal_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'signals'
    directory.mkdir()
    monkeypatch.setattr(jobs_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(directory))
    return directory


def _names(directory) -> List[str]:
    return sorted(os.listdir(directory))


def _make(directory, name: str, age_seconds: float = 0, content: str = ''):
    path = directory / name
    path.write_text(content, encoding='utf-8')
    if age_seconds:
        modified = time.time() - age_seconds
        os.utime(path, (modified, modified))
    return path


# --------------------------------------------------------------------------
# Writing and taking a signal
# --------------------------------------------------------------------------


@pytest.mark.parametrize('content', ['', 'graceful', 'graceful:60'])
def test_write_leaves_only_the_signal(signal_dir, content):
    cancel_signal.write(42, content)

    # In particular no lock file and no temporary file.
    assert _names(signal_dir) == ['42']
    assert (signal_dir / '42').read_text(encoding='utf-8') == content


def test_cancelling_a_job_leaves_one_file(signal_dir):
    """`sky jobs cancel` leaves the signal of the job in the directory, and
    nothing else. It used to leave a lock file too, forever."""
    with mock.patch.object(managed_job_state, 'is_legacy_controller_process',
                           return_value=False), \
         mock.patch.object(
             managed_job_state, 'get_status',
             return_value=managed_job_state.ManagedJobStatus.RUNNING), \
         mock.patch.object(managed_job_utils, 'update_managed_jobs_statuses'), \
         mock.patch.object(managed_job_state, 'get_workspace',
                           return_value='default'):
        managed_job_utils.cancel_jobs_by_id(job_ids=[42],
                                            current_workspace='default',
                                            graceful=True,
                                            graceful_timeout=60)

    assert _names(signal_dir) == ['42']
    assert cancel_signal.take(42) == 'graceful:60'
    assert _names(signal_dir) == []


def test_write_replaces_an_earlier_signal(signal_dir):
    cancel_signal.write(42, 'graceful:60')
    cancel_signal.write(42, '')

    assert _names(signal_dir) == ['42']
    assert (signal_dir / '42').read_text(encoding='utf-8') == ''


def test_signal_is_never_visible_half_written(signal_dir):
    """A reader that looks while the content is being written sees no signal
    at all: the signal only gets its name once it is complete."""
    seen_while_writing = []
    real_replace = os.replace

    def look_then_replace(source, destination):
        seen_while_writing.append(
            (cancel_signal.exists(42),
             [name for name in os.listdir(signal_dir) if name.isdigit()]))
        return real_replace(source, destination)

    with mock.patch.object(os, 'replace', side_effect=look_then_replace):
        cancel_signal.write(42, 'graceful:60')

    assert seen_while_writing == [(False, [])]
    assert cancel_signal.take(42) == 'graceful:60'


def test_failed_write_leaves_nothing_behind(signal_dir):
    with mock.patch.object(os, 'replace', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            cancel_signal.write(42, 'graceful')

    assert _names(signal_dir) == []


def test_write_makes_a_missing_directory(tmp_path, monkeypatch):
    """The file lock that writers used to take made the directory. A server
    replica that never ran a controller may not have it."""
    directory = tmp_path / 'not' / 'there'
    monkeypatch.setattr(jobs_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(directory))

    cancel_signal.write(42, 'graceful')

    assert _names(directory) == ['42']


@pytest.mark.parametrize('content', ['', 'graceful', 'graceful:60'])
def test_take_returns_the_content_and_removes_the_signal(signal_dir, content):
    cancel_signal.write(42, content)
    assert cancel_signal.exists(42)

    assert cancel_signal.take(42) == content

    assert not cancel_signal.exists(42)
    assert _names(signal_dir) == []


def test_take_without_a_signal(signal_dir):
    assert cancel_signal.take(42) is None
    assert _names(signal_dir) == []


def test_signal_written_during_a_take_is_left_in_place(signal_dir):
    """A second cancel that lands while the first is being taken must not be
    removed with it, unread. The file lock used to see to that."""
    cancel_signal.write(42, 'graceful:60')
    real_open = open
    second_written = []

    def open_then_write_a_second_signal(path, mode='r', **kwargs):
        if 'r' not in mode or second_written:
            return real_open(path, mode, **kwargs)
        # The controller reads the first signal. The second one lands right
        # after that, before the controller removes what it has read.
        with real_open(path, mode, **kwargs) as f:
            first = f.read()
        second_written.append(True)
        cancel_signal.write(42, '')
        return io.StringIO(first)

    with mock.patch.object(cancel_signal,
                           'open',
                           open_then_write_a_second_signal,
                           create=True):
        assert cancel_signal.take(42) == 'graceful:60'

    assert second_written
    assert _names(signal_dir) == ['42']
    assert cancel_signal.take(42) == ''
    assert _names(signal_dir) == []


def test_take_survives_a_rename_reported_as_failed(signal_dir):
    """A network filesystem can apply a rename and still report that the file
    was not there, when the request was sent twice."""
    cancel_signal.write(42, 'graceful')
    real_rename = os.rename

    def rename_then_report_failure(source, destination):
        real_rename(source, destination)
        raise FileNotFoundError(source)

    with mock.patch.object(os, 'rename', rename_then_report_failure):
        assert cancel_signal.take(42) == 'graceful'

    assert _names(signal_dir) == []


def test_take_reads_the_signal_whole_from_an_older_writer(signal_dir):
    """During a rolling update an older API server still writes a signal in
    place, under its lock. A controller that takes the signal in the middle
    of that write waits for the lock and reads the whole content."""
    signal_path = signal_dir / '42'
    file_open = threading.Event()
    finish_writing = threading.Event()

    def older_writer():
        with filelock.FileLock(str(signal_path) + '.lock'):
            # As pathlib's write_text does, with a pause in the middle.
            with signal_path.open('w', encoding='utf-8') as f:
                file_open.set()
                finish_writing.wait(timeout=10)
                f.write('graceful:60')

    writer = threading.Thread(target=older_writer)
    writer.start()
    assert file_open.wait(timeout=10)
    taken = []
    reader = threading.Thread(
        target=lambda: taken.append(cancel_signal.take(42)))
    reader.start()
    # The controller waits for the lock; it is not done with an empty signal.
    reader.join(timeout=0.5)
    assert reader.is_alive()
    finish_writing.set()
    writer.join(timeout=10)
    reader.join(timeout=10)

    assert taken == ['graceful:60']
    # The lock file is left to the garbage collector.
    assert _names(signal_dir) == ['42.lock']


def test_signal_of_an_older_writer_that_starts_during_a_take_is_left_in_place(
        signal_dir):
    """An older writer that takes its lock just after the controller looked
    for it writes a new signal file: the one being taken has a private name
    by then. Neither signal is read half-written, and neither is lost."""
    signal_path = signal_dir / '42'
    cancel_signal.write(42, 'graceful')
    real_exists = os.path.exists
    older_writers = []

    def exists_then_older_writer_starts(path):
        found = real_exists(path)
        if str(path).endswith('.lock') and not older_writers:
            lock = filelock.FileLock(str(path))
            lock.acquire()
            # Opened and truncated, but not written yet.
            older_writers.append((lock, signal_path.open('w',
                                                         encoding='utf-8')))
        return found

    with mock.patch.object(cancel_signal.os.path, 'exists',
                           exists_then_older_writer_starts):
        assert cancel_signal.take(42) == 'graceful'

    lock, signal_file = older_writers[0]
    signal_file.write('graceful:60')
    signal_file.close()
    lock.release()
    assert _names(signal_dir) == ['42', '42.lock']
    assert cancel_signal.take(42) == 'graceful:60'


def test_remove(signal_dir):
    _make(signal_dir, '42')
    _make(signal_dir, '42.lock')
    _make(signal_dir, '43')

    cancel_signal.remove(42)
    cancel_signal.remove(44)  # Nothing to remove is fine.

    # Lock files are only removed by the garbage collector, once their job
    # is over.
    assert _names(signal_dir) == ['42.lock', '43']


# --------------------------------------------------------------------------
# Stale files
# --------------------------------------------------------------------------


def test_remove_stale_files(signal_dir):
    unfinished_job_ids = [7, 8]
    # Lock files that older writers left behind. Job 8 may still be
    # cancelled by such a writer, which takes this lock.
    _make(signal_dir, '1.lock')
    _make(signal_dir, '2.lock', age_seconds=_OLD)
    _make(signal_dir, '8.lock', age_seconds=_OLD)
    # Temporary files: of a writer that died, and of one that is writing.
    _make(signal_dir, '3.0123abcd.tmp', age_seconds=_OLD)
    _make(signal_dir, '4.0123abcd.tmp')
    # Signals of jobs that are over: no controller process looks at them.
    _make(signal_dir, '5', age_seconds=_OLD)
    _make(signal_dir, '5.lock', age_seconds=_OLD)
    _make(signal_dir, '6')
    # Signals of unfinished jobs, however old: job 7 may be waiting for a
    # controller process, which takes the signal when it starts the job.
    _make(signal_dir, '7', age_seconds=_OLD)
    _make(signal_dir, '7.lock', age_seconds=_OLD)
    _make(signal_dir, '8')
    # Files of other components, and files nobody knows.
    others = [
        'sky_serve_controller_signal_my-service',
        'sky_serve_controller_signal_my-service.lock', 'waiting_jobs.marker',
        'notes.txt', 'other.lock', 'backup.tmp'
    ]
    for name in others:
        _make(signal_dir, name, age_seconds=_OLD)

    with mock.patch.object(managed_job_state,
                           'get_nonterminal_job_ids_by_name',
                           return_value=unfinished_job_ids) as unfinished:
        removed = cancel_signal.remove_stale_files()

    assert removed == 6
    assert _names(signal_dir) == sorted(
        ['4.0123abcd.tmp', '7', '7.lock', '8', '8.lock'] + others)
    unfinished.assert_called_once_with(None, all_users=True)


def test_remove_stale_files_does_not_look_at_each_file(signal_dir):
    """Older code left one lock file per cancelled job: tens of thousands on
    a busy server. On a network filesystem, a look at each of them takes far
    longer than removing them, so which jobs are over is asked once."""
    for job_id in range(1, 201):
        _make(signal_dir, f'{job_id}.lock')
    _make(signal_dir, '201')

    def no_look(path, *args, **kwargs):
        raise AssertionError(f'looked at {path}')

    with mock.patch.object(managed_job_state,
                           'get_nonterminal_job_ids_by_name',
                           return_value=[201]) as unfinished, \
         mock.patch.object(cancel_signal.os, 'stat', no_look):
        assert cancel_signal.remove_stale_files() == 200

    assert _names(signal_dir) == ['201']
    unfinished.assert_called_once_with(None, all_users=True)


def test_remove_stale_files_asks_nothing_without_a_job_file(signal_dir):
    _make(signal_dir, '3.0123abcd.tmp', age_seconds=_OLD)
    _make(signal_dir, 'waiting_jobs.marker')

    with mock.patch.object(managed_job_state,
                           'get_nonterminal_job_ids_by_name') as unfinished:
        assert cancel_signal.remove_stale_files() == 1

    assert _names(signal_dir) == ['waiting_jobs.marker']
    unfinished.assert_not_called()


def test_remove_stale_files_without_a_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_constants, 'CONSOLIDATED_SIGNAL_PATH',
                        str(tmp_path / 'missing'))

    assert cancel_signal.remove_stale_files() == 0


def test_stale_files_are_removed_by_the_elected_garbage_collector():
    """One controller process is elected to garbage collect. The signal
    directory must be cleaned by that one, not by every controller process."""
    from sky.jobs import log_gc  # pylint: disable=import-outside-toplevel

    started = []

    class _Thread:

        def __init__(self, target, daemon):
            del daemon
            self._target = target

        def start(self):
            started.append(self._target)

        def join(self):
            pass

    with mock.patch.object(log_gc.threading, 'Thread', _Thread), \
         mock.patch.object(log_gc.context, 'get'), \
         mock.patch.object(log_gc.os, 'makedirs'), \
         mock.patch.object(log_gc.pathlib.Path, 'unlink'):
        log_gc.run_log_gc.__wrapped__()

    assert cancel_signal.gc_stale_files in started


# --------------------------------------------------------------------------
# The controller process
# --------------------------------------------------------------------------


class _StopLoop(Exception):
    pass


def _record_calling_thread(function, threads: List[str]):

    def wrapper(*args, **kwargs):
        threads.append(threading.current_thread().name)
        return function(*args, **kwargs)

    return wrapper


def _all_in_the_signal_pool(threads: List[str]) -> bool:
    """Whether calls were made, all in the pool kept for the signal directory:
    not on the event loop, and not in the pool that asyncio.to_thread shares
    with every blocking call of every job."""
    return bool(threads) and all(
        name.startswith('cancel-signal') for name in threads)


async def _finishes_within(awaitable, seconds: float):
    """Awaits something, failing the test if it takes longer than given."""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
        pytest.fail(f'Not finished within {seconds} s.')
    return task.result()


async def _run_cancel_loop_once(manager) -> None:
    """Runs one pass of cancel_job(), up to its sleep."""

    async def stop(seconds):
        del seconds
        raise _StopLoop()

    with mock.patch.object(controller_module.asyncio, 'sleep', new=stop):
        with pytest.raises(_StopLoop):
            await manager.cancel_job()


@pytest.mark.asyncio
async def test_controller_takes_signals_off_the_event_loop(signal_dir):
    """The directory listing and the signal file calls run in a thread of the
    pool kept for them, and the event loop keeps running while they do."""
    manager = controller_module.ControllerManager('test-uuid')
    job_task = mock.MagicMock()
    manager.job_tasks[7] = job_task
    cancel_signal.write(7, 'graceful:30')
    _make(signal_dir, '7.lock')  # As left by an older writer.
    cancel_signal.write(8, '')  # A job of another controller process.
    _make(signal_dir, 'waiting_jobs.marker')

    listing_threads: List[str] = []
    take_threads: List[str] = []
    ticks_during_listing = []
    real_listdir = os.listdir

    def slow_listdir(path):
        listing_threads.append(threading.current_thread().name)
        # Long enough for the event loop to prove it is not blocked.
        time.sleep(0.3)
        return real_listdir(path)

    async def tick():
        while not listing_threads:
            await asyncio.sleep(0)
        started = time.monotonic()
        while time.monotonic() - started < 0.2:
            ticks_during_listing.append(time.monotonic())
            await asyncio.sleep(0.01)

    real_sleep = asyncio.sleep

    async def stop_after_the_pass(seconds):
        if seconds == 15:
            raise _StopLoop()
        await real_sleep(seconds)

    ticker = asyncio.create_task(tick())
    with mock.patch.object(controller_module.os, 'listdir', slow_listdir), \
         mock.patch.object(
             controller_module.cancel_signal, 'take',
             _record_calling_thread(cancel_signal.take, take_threads)), \
         mock.patch.object(controller_module.asyncio, 'sleep',
                           new=stop_after_the_pass):
        with pytest.raises(_StopLoop):
            await manager.cancel_job()
    await ticker

    assert _all_in_the_signal_pool(listing_threads)
    assert _all_in_the_signal_pool(take_threads)
    # The loop ran other work during the 0.3 s listing.
    assert len(ticks_during_listing) >= 5
    # Job 7 was cancelled with the settings of its signal, and the signal is
    # gone. Job 8 is not run here, so its signal is not touched. The lock file
    # is left to the garbage collector.
    job_task.cancel.assert_called_once_with()
    assert manager._cancel_info[7] == (True, 30)  # pylint: disable=protected-access
    assert _names(signal_dir) == ['7.lock', '8', 'waiting_jobs.marker']


@pytest.mark.asyncio
async def test_cancel_is_taken_while_the_shared_thread_pool_is_full(signal_dir):
    """Every blocking call of every job in a controller process goes through
    asyncio.to_thread's default executor. When slow calls have all of its
    workers (many jobs tearing down at once, a stuck API server), a cancel
    must still be noticed and applied."""
    del signal_dir
    manager = controller_module.ControllerManager('test-uuid')
    job_task = mock.MagicMock()
    manager.job_tasks[7] = job_task
    cancel_signal.write(7, 'graceful:30')

    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=2))
    release = threading.Event()
    slow_calls = [
        asyncio.ensure_future(asyncio.to_thread(release.wait, 30))
        for _ in range(2)
    ]
    # A third call cannot start: the pool is full.
    queued_call = asyncio.ensure_future(asyncio.to_thread(lambda: 'ran'))
    try:
        await asyncio.sleep(0.2)
        assert not queued_call.done()

        await _finishes_within(_run_cancel_loop_once(manager), 10)

        job_task.cancel.assert_called_once_with()
        assert manager._cancel_info[7] == (True, 30)  # pylint: disable=protected-access
        assert not queued_call.done()
    finally:
        release.set()
        await asyncio.gather(*slow_calls, queued_call)


@pytest.mark.asyncio
async def test_hung_signal_directory_takes_no_shared_thread(signal_dir):
    """A listing that does not return (a hung network filesystem) stops the
    cancel loop, and only that: it holds no worker of the pool that the jobs
    share, and the loop does not send further calls after it."""
    del signal_dir
    manager = controller_module.ControllerManager('test-uuid')
    loop = asyncio.get_running_loop()
    # One worker: if the listing took it, nothing else could run.
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=1))
    listing_threads: List[str] = []
    release = threading.Event()

    def hung_listdir(path):
        del path
        listing_threads.append(threading.current_thread().name)
        release.wait(30)
        return []

    cancel_loop = None
    try:
        with mock.patch.object(controller_module.os, 'listdir', hung_listdir):
            cancel_loop = asyncio.ensure_future(manager.cancel_job())
            while not listing_threads:
                await asyncio.sleep(0.01)

            assert await _finishes_within(asyncio.to_thread(lambda: 'ran'),
                                          10) == 'ran'
            # Well past the moment a retry would have come.
            await asyncio.sleep(0.3)
            assert not cancel_loop.done()
            assert len(listing_threads) == 1
            assert _all_in_the_signal_pool(listing_threads)
    finally:
        release.set()
        if cancel_loop is not None:
            cancel_loop.cancel()
            await asyncio.gather(cancel_loop, return_exceptions=True)


@pytest.mark.asyncio
async def test_controller_skips_a_signal_that_is_gone(signal_dir):
    """A signal can go between the listing and the take (the garbage
    collector removes the signal of a job that is over)."""
    manager = controller_module.ControllerManager('test-uuid')
    job_task = mock.MagicMock()
    manager.job_tasks[7] = job_task
    real_listdir = os.listdir

    def listing_with_a_vanished_signal(path):
        return real_listdir(path) + ['7']

    with mock.patch.object(controller_module.os, 'listdir',
                           listing_with_a_vanished_signal):
        await _run_cancel_loop_once(manager)

    job_task.cancel.assert_not_called()
    assert 7 not in manager._cancel_info  # pylint: disable=protected-access
    assert _names(signal_dir) == []


@pytest.mark.asyncio
async def test_claimed_job_is_checked_without_listing(signal_dir):
    """A job that was cancelled while it waited is cancelled when a controller
    process claims it. The check is one file lookup in a thread of the signal
    pool: it does not list the directory, so it does not cost more when the
    directory is big."""
    manager = controller_module.ControllerManager('test-uuid')
    manager.start_job = mock.AsyncMock()
    cancel_signal.write(7, '')
    _make(signal_dir, '7.lock')  # As left by an older writer.
    cancel_signal.write(8, '')  # Not the claimed job.
    waiting = [{'job_id': 7, 'pool': None}]

    async def claim(**kwargs):
        del kwargs
        return waiting.pop() if waiting else None

    async def stop_when_idle(seconds):
        assert seconds == 10, 'expected the no-waiting-job sleep'
        raise _StopLoop()

    def no_listing(path):
        raise AssertionError(f'{path} was listed')

    lookup_threads: List[str] = []
    removal_threads: List[str] = []
    set_cancelled = mock.AsyncMock()
    with mock.patch.object(controller_module.os, 'listdir', no_listing), \
         mock.patch.object(
             controller_module.cancel_signal, 'exists',
             _record_calling_thread(cancel_signal.exists, lookup_threads)), \
         mock.patch.object(
             controller_module.cancel_signal, 'remove',
             _record_calling_thread(cancel_signal.remove, removal_threads)), \
         mock.patch.object(controller_module.controller_utils,
                           'get_number_of_jobs_controllers', return_value=1), \
         mock.patch.object(controller_module.metrics_lib, 'METRICS_ENABLED',
                           False), \
         mock.patch.object(controller_module.managed_job_utils,
                           'event_callback_func'), \
         mock.patch.object(managed_job_state, 'get_waiting_job_async',
                           new=claim), \
         mock.patch.object(
             managed_job_state, 'get_status_async',
             new=mock.AsyncMock(
                 return_value=managed_job_state.ManagedJobStatus.PENDING)), \
         mock.patch.object(managed_job_state, 'set_cancelling_async',
                           new=mock.AsyncMock()), \
         mock.patch.object(managed_job_state, 'set_cancelled_async',
                           new=set_cancelled), \
         mock.patch.object(controller_module.asyncio, 'sleep',
                           new=stop_when_idle):
        with pytest.raises(_StopLoop):
            await manager.monitor_loop()

    manager.start_job.assert_not_called()
    assert set_cancelled.await_args.kwargs['job_id'] == 7
    assert _all_in_the_signal_pool(lookup_threads)
    assert _all_in_the_signal_pool(removal_threads)
    assert _names(signal_dir) == ['7.lock', '8']

"""Tests for the cancel signal files of managed jobs.

The signal directory can be on a network filesystem shared by API server
replicas, where a call can take seconds and a listing costs more the more
entries there are. These tests pin down that:

  - writing or taking a signal leaves no file behind;
  - a controller process never calls into the directory on its event loop;
  - the files that older code left behind are cleaned up, and nothing else.
"""
import asyncio
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

_OLD = cancel_signal._STALE_AFTER_SECONDS + 600  # pylint: disable=protected-access


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


def test_take_removes_the_lock_file_of_an_older_writer(signal_dir):
    """Writers used to hold '<job id>.lock' while writing, and it was never
    removed: one file was left in the directory for every cancelled job."""
    _make(signal_dir, '42', content='graceful:60')
    _make(signal_dir, '42.lock')

    assert cancel_signal.take(42) == 'graceful:60'

    assert _names(signal_dir) == []


def test_take_waits_for_an_older_writer_that_holds_the_lock(signal_dir):
    """During a rolling update an older API server may still write a signal
    in place, under its lock. Its signal must be read whole."""
    signal_path = signal_dir / '42'
    lock_held = threading.Event()
    release_writer = threading.Event()

    def older_writer():
        with filelock.FileLock(str(signal_path) + '.lock'):
            # Created empty first: an empty signal means a plain cancel.
            signal_path.touch()
            lock_held.set()
            release_writer.wait(timeout=10)
            signal_path.write_text('graceful:60', encoding='utf-8')

    writer = threading.Thread(target=older_writer)
    writer.start()
    assert lock_held.wait(timeout=10)
    taken = []
    reader = threading.Thread(
        target=lambda: taken.append(cancel_signal.take(42)))
    reader.start()
    # The reader is blocked on the lock, not done with an empty signal.
    reader.join(timeout=0.5)
    assert reader.is_alive()
    release_writer.set()
    writer.join(timeout=10)
    reader.join(timeout=10)

    assert taken == ['graceful:60']
    assert _names(signal_dir) == []


def test_remove(signal_dir):
    _make(signal_dir, '42')
    _make(signal_dir, '42.lock')
    _make(signal_dir, '43')

    cancel_signal.remove(42)
    cancel_signal.remove(44)  # Nothing to remove is fine.

    assert _names(signal_dir) == ['43']


# --------------------------------------------------------------------------
# Stale files
# --------------------------------------------------------------------------


def test_remove_stale_files(signal_dir):
    unfinished_job_ids = [7, 8]
    # Lock files left by older writers.
    _make(signal_dir, '1.lock', age_seconds=_OLD)
    _make(signal_dir, '2.lock')  # Recent: may be in use right now.
    # Temporary files of writers that died.
    _make(signal_dir, '3.0123abcd.tmp', age_seconds=_OLD)
    _make(signal_dir, '4.0123abcd.tmp')  # Recent: being written.
    # Signals.
    _make(signal_dir, '5', age_seconds=_OLD)  # Job 5 is over.
    _make(signal_dir, '5.lock', age_seconds=_OLD)
    _make(signal_dir, '6')  # Job 6 is over too, but the signal is recent.
    _make(signal_dir, '7', age_seconds=_OLD)  # Job 7 still waits to start.
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

    assert removed == 3
    assert _names(signal_dir) == sorted(
        ['2.lock', '4.0123abcd.tmp', '6', '7', '7.lock', '8'] + others)
    unfinished.assert_called_once_with(None, all_users=True)


def test_remove_stale_files_asks_for_jobs_only_for_an_old_signal(signal_dir):
    for job_id in range(1, 6):
        _make(signal_dir, f'{job_id}.lock', age_seconds=_OLD)
    _make(signal_dir, '6')

    with mock.patch.object(managed_job_state,
                           'get_nonterminal_job_ids_by_name') as unfinished:
        assert cancel_signal.remove_stale_files() == 5

    assert _names(signal_dir) == ['6']
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


def _record_calling_thread(function, threads: List[int]):

    def wrapper(*args, **kwargs):
        threads.append(threading.get_ident())
        return function(*args, **kwargs)

    return wrapper


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
    """The directory listing and the signal file calls run in a thread, and
    the event loop keeps running while they do."""
    manager = controller_module.ControllerManager('test-uuid')
    job_task = mock.MagicMock()
    manager.job_tasks[7] = job_task
    cancel_signal.write(7, 'graceful:30')
    _make(signal_dir, '7.lock')  # As left by an older writer.
    cancel_signal.write(8, '')  # A job of another controller process.
    _make(signal_dir, 'waiting_jobs.marker')

    loop_thread = threading.get_ident()
    listing_threads: List[int] = []
    take_threads: List[int] = []
    ticks_during_listing = []
    real_listdir = os.listdir

    def slow_listdir(path):
        listing_threads.append(threading.get_ident())
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

    assert listing_threads and loop_thread not in listing_threads
    assert take_threads and loop_thread not in take_threads
    # The loop ran other work during the 0.3 s listing.
    assert len(ticks_during_listing) >= 5
    # Job 7 was cancelled with the settings of its signal, and nothing of the
    # signal is left. Job 8 is not run here, so its signal is not touched.
    job_task.cancel.assert_called_once_with()
    assert manager._cancel_info[7] == (True, 30)  # pylint: disable=protected-access
    assert _names(signal_dir) == ['8', 'waiting_jobs.marker']


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
    process claims it. The check is one file lookup in a thread: it does not
    list the directory, so it does not cost more when the directory is big."""
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

    loop_thread = threading.get_ident()
    lookup_threads: List[int] = []
    removal_threads: List[int] = []
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
    assert lookup_threads and loop_thread not in lookup_threads
    assert removal_threads and loop_thread not in removal_threads
    assert _names(signal_dir) == ['8']

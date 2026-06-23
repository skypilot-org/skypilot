"""Unit tests for subprocess_utils.py."""
import logging
import multiprocessing
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

import psutil
import pytest

from sky.utils import subprocess_utils

logger = logging.getLogger(__name__)

# Fixtures to replace setUp and tearDown


@pytest.fixture
def mock_startable():
    """Create a mock class that implements the Startable protocol."""

    def create_mock():
        return mock.MagicMock(spec=subprocess_utils.Startable)

    mock_factory = mock.MagicMock()
    mock_factory.side_effect = create_mock
    return mock_factory


@pytest.fixture
def mock_sleep():
    """Patch time.sleep to avoid actual delays during tests."""
    with mock.patch('time.sleep') as mock_sleep:
        yield mock_sleep


@pytest.fixture
def mock_cpu_count():
    """Patch common_utils.get_cpu_count to return a predictable value."""
    with mock.patch('sky.utils.common_utils.get_cpu_count',
                    return_value=16) as mock_count:
        yield mock_count


# Test functions to replace test methods


def test_empty_process_list(mock_sleep):
    """Test with an empty process list."""
    processes = []
    subprocess_utils.slow_start_processes(processes)
    # No processes to start, so sleep should not be called
    mock_sleep.assert_not_called()


def test_single_process(mock_startable, mock_sleep):
    """Test with a single process."""
    process = mock_startable()
    processes = [process]

    subprocess_utils.slow_start_processes(processes)

    # Process should be started
    process.start.assert_called_once()
    # No sleep should be called with only one process
    mock_sleep.assert_not_called()


def test_multiple_processes(mock_startable, mock_sleep, mock_cpu_count):
    """Test with multiple processes."""
    mock_cpu_count.return_value = 16
    processes = [mock_startable() for _ in range(5)]

    subprocess_utils.slow_start_processes(processes)

    # All processes should be started
    for process in processes:
        process.start.assert_called_once()

    # Sleep should be called the correct number of times
    # With 5 processes and batch sizes of 1, 2, 2 (based on the logs)
    # We expect 2 sleep calls
    assert mock_sleep.call_count == 2


def test_custom_delay(mock_startable, mock_sleep):
    """Test with a custom delay value."""
    processes = [mock_startable() for _ in range(3)]
    custom_delay = 5.0

    subprocess_utils.slow_start_processes(processes, delay=custom_delay)

    # Sleep should be called with the custom delay
    mock_sleep.assert_called_with(custom_delay)


def test_on_start_callback(mock_startable):
    """Test the on_start callback functionality."""
    processes = [mock_startable() for _ in range(3)]
    on_start_mock = mock.Mock()

    subprocess_utils.slow_start_processes(processes, on_start=on_start_mock)

    # All processes should be started
    for process in processes:
        process.start.assert_called_once()

    # Verify callback was called with each process
    calls = [mock.call(process) for process in processes]
    on_start_mock.assert_has_calls(calls)


def test_batch_size_growth(mock_startable, mock_sleep):
    """Test that batch size grows exponentially but is limited by max_batch_size."""
    # Create enough processes to test batch size growth
    processes = [mock_startable() for _ in range(32)]

    # Mock the implementation to capture batch sizes
    batch_sizes = []

    def mock_sleep_side_effect(delay):
        # Calculate the current batch size based on the number of started processes
        started_count = sum(p.start.called for p in processes)
        batch_sizes.append(started_count)
        # Don't actually sleep or call original_sleep to avoid recursion

    mock_sleep.side_effect = mock_sleep_side_effect

    # Mock get_cpu_count to return an integer to avoid float issues
    with mock.patch('sky.utils.subprocess_utils.common_utils.get_cpu_count',
                    return_value=16):
        subprocess_utils.slow_start_processes(processes)

    # With CPU count of 16, max_batch_size = 8
    # So we expect batch sizes to grow: 1, 2, 4, 8, 8, 8, ...
    # Verify the expected pattern of batch size growth
    expected_started_counts = [1, 3, 7, 15, 23, 31]
    assert batch_sizes == expected_started_counts


def test_with_low_cpu_count(mock_startable, mock_sleep, mock_cpu_count):
    """Test with a low CPU count to verify max_batch_size behavior."""
    # Temporarily change the CPU count to a lower value
    mock_cpu_count.return_value = 2

    # Create processes
    processes = [mock_startable() for _ in range(5)]

    # Mock the implementation to capture batch sizes
    batch_sizes = []

    def mock_sleep_side_effect(delay):
        # Calculate the current batch size based on the number of started processes
        started_count = sum(p.start.called for p in processes)
        batch_sizes.append(started_count)
        return None  # Don't actually sleep

    mock_sleep.side_effect = mock_sleep_side_effect

    subprocess_utils.slow_start_processes(processes)

    # Start 1 at a time
    expected_started_counts = [1, 2, 3, 4]
    assert batch_sizes == expected_started_counts


def _ignore_sigterm():
    """Process function that ignores SIGTERM signal."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)


class TestKillProcessWithGrace(unittest.TestCase):
    """Test cases for kill_process_with_grace function."""

    def setUp(self):
        """Set up test cases."""
        self.processes = []

    def tearDown(self):
        """Clean up any remaining processes."""
        for process in self.processes:
            try:
                if isinstance(process, psutil.Process):
                    if process.is_running():
                        process.kill()
                else:
                    if process.is_alive():
                        process.kill()
                # Wait for the process to be fully terminated
                time.sleep(0.1)
            except (psutil.NoSuchProcess, ValueError):
                pass

    def _create_dummy_process(self):
        """Create a dummy process that sleeps."""
        process = multiprocessing.Process(target=time.sleep, args=(30,))
        self.processes.append(process)
        return process

    def _wait_for_process_start(self, process, timeout=1):
        """Wait for process to start and stabilize."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if isinstance(process, psutil.Process):
                    if process.is_running():
                        return True
                else:
                    if process.is_alive():
                        return True
            except (psutil.NoSuchProcess, ValueError):
                pass
            time.sleep(0.1)
        return False

    def test_normal_termination(self):
        """Test normal process termination with SIGTERM."""
        process = self._create_dummy_process()
        process.start()
        self.assertTrue(self._wait_for_process_start(process))

        # Kill the process with grace period
        subprocess_utils.kill_process_with_grace_period(process,
                                                        force=False,
                                                        grace_period=2)

        # Process should be terminated
        self.assertFalse(process.is_alive())

    def test_force_kill(self):
        """Test force kill with SIGKILL."""
        process = self._create_dummy_process()
        process.start()
        self.assertTrue(self._wait_for_process_start(process))

        # Force kill the process
        subprocess_utils.kill_process_with_grace_period(process,
                                                        force=True,
                                                        grace_period=2)

        # Process should be terminated
        self.assertFalse(process.is_alive())

    def test_psutil_process(self):
        """Test killing process using psutil.Process."""
        process = self._create_dummy_process()
        process.start()
        self.assertTrue(self._wait_for_process_start(process))

        psutil_proc = psutil.Process(process.pid)
        self.processes.append(psutil_proc)
        self.assertTrue(self._wait_for_process_start(psutil_proc))

        # Kill the process with grace period
        subprocess_utils.kill_process_with_grace_period(psutil_proc,
                                                        force=False,
                                                        grace_period=2)

        self.assertFalse(psutil_proc.is_running())

    def test_already_dead(self):
        """Test killing an already dead process."""
        process = self._create_dummy_process()
        process.start()
        self.assertTrue(self._wait_for_process_start(process))
        process.terminate()
        time.sleep(0.1)  # Give some time for the process to terminate

        # This should not raise any exception
        subprocess_utils.kill_process_with_grace_period(process,
                                                        force=False,
                                                        grace_period=1)
        self.assertFalse(process.is_alive())

    def test_timeout(self):
        """Test process that ignores SIGTERM and requires SIGKILL."""
        process = multiprocessing.Process(target=_ignore_sigterm)
        self.processes.append(process)
        process.start()
        self.assertTrue(self._wait_for_process_start(process))

        # Try to kill with SIGTERM first
        subprocess_utils.kill_process_with_grace_period(process,
                                                        force=False,
                                                        grace_period=1)

        # Process should be terminated by SIGKILL after grace period
        time.sleep(0.1)  # Give some time for the process to be fully terminated
        self.assertFalse(process.is_alive())


class TestSafeChildren(unittest.TestCase):
    """Tests for _safe_children: the tolerant wrapper around
    psutil.Process.children(recursive=True).

    Three cases matter:
      1. Happy path — pass through psutil unchanged.
      2. psutil raises AccessDenied — log the offending PID and fall
         back to the /proc walk.
      3. Fallback /proc walk silently ignores unreadable PIDs (the
         simplified version of the previous "denied diagnostics" path).
    """

    def test_happy_path_delegates_to_psutil(self):
        # If psutil succeeds, _safe_children must return exactly what
        # psutil returned (no extra work).
        proc = mock.MagicMock(spec=psutil.Process)
        sentinel = [mock.MagicMock(), mock.MagicMock()]
        proc.children.return_value = sentinel
        result = subprocess_utils._safe_children(proc)
        proc.children.assert_called_once_with(recursive=True)
        self.assertIs(result, sentinel)

    def test_access_denied_triggers_fallback_with_log(self):
        proc = mock.MagicMock(spec=psutil.Process)
        proc.pid = 100
        proc.create_time.return_value = 1000.0
        proc.children.side_effect = psutil.AccessDenied(pid=200,
                                                        name='foo',
                                                        msg='denied')
        fallback_sentinel = [mock.MagicMock()]
        with mock.patch.object(subprocess_utils,
                               '_fallback_children',
                               return_value=fallback_sentinel) as mock_fb:
            with mock.patch.object(subprocess_utils,
                                   '_pid_diag',
                                   return_value={
                                       'uid': '1337',
                                       'exe': '/usr/sbin/agent'
                                   }):
                with self.assertLogs(subprocess_utils.logger.name,
                                     level='WARNING') as cm:
                    result = subprocess_utils._safe_children(proc)
        # parent_ctime must be plumbed through so the fallback can
        # reject PID-reuse phantoms (mirrors psutil.Process.children).
        mock_fb.assert_called_once_with(100, 1000.0)
        self.assertIs(result, fallback_sentinel)
        # The warning must include the offending PID + the signals we
        # could still extract via dir-getattr / lnk_file-read so future
        # debugging has a starting point.
        joined = '\n'.join(cm.output)
        self.assertIn('pid=200', joined)
        self.assertIn('uid=1337', joined)
        self.assertIn('/usr/sbin/agent', joined)

    def test_access_denied_then_parent_gone_returns_empty(self):
        # If the parent process disappears between the AccessDenied and
        # the create_time snapshot, we can't run the PID-reuse defense,
        # so the safest behaviour is to return [].
        proc = mock.MagicMock(spec=psutil.Process)
        proc.pid = 100
        proc.children.side_effect = psutil.AccessDenied(pid=200)
        proc.create_time.side_effect = psutil.NoSuchProcess(pid=100)
        with mock.patch.object(subprocess_utils,
                               '_fallback_children') as mock_fb:
            with mock.patch.object(subprocess_utils,
                                   '_pid_diag',
                                   return_value={}):
                with self.assertLogs(subprocess_utils.logger.name,
                                     level='WARNING'):
                    result = subprocess_utils._safe_children(proc)
        mock_fb.assert_not_called()
        self.assertEqual(result, [])

    def test_no_such_process_returns_empty_silently(self):
        proc = mock.MagicMock(spec=psutil.Process)
        proc.children.side_effect = psutil.NoSuchProcess(pid=1)
        self.assertEqual(subprocess_utils._safe_children(proc), [])


class TestFallbackChildren(unittest.TestCase):
    """Tests for _fallback_children, the simplified /proc walk used when
    psutil's ppid_map raises AccessDenied. PIDs we can't read are
    silently skipped (no diag collection) — the initial offending PID
    was already logged by _safe_children."""

    def setUp(self):
        # Force Linux branch on any host.
        self._platform_patch = mock.patch.object(subprocess_utils.sys,
                                                 'platform', 'linux')
        self._platform_patch.start()
        self.addCleanup(self._platform_patch.stop)

    @staticmethod
    def _stat_bytes(pid: int, ppid: int, comm: str = 'cmd') -> bytes:
        return (f'{pid} ({comm}) S {ppid} 0 0 0 -1 4194304 0 0 0 0 0 0 0 0 '
                f'20 0 1 0 0 0 0 0 0').encode()

    def _fake_proc(self, pid: int, ctime: float):
        # psutil.Process(pid) returns an object with .pid and .create_time().
        m = mock.MagicMock(spec=psutil.Process, pid=pid)
        m.create_time.return_value = ctime
        return m

    def test_returns_descendants_skipping_unreadable(self):
        # Tree: 100 → {200 → {300}, 201}. 200 is unreadable (EACCES);
        # the fallback should still return 201 (sibling of 200) but
        # cannot reach 300 (whose only path is through 200).
        tree = {100: 1, 200: 100, 201: 100, 300: 200, 999: 1}

        def fake_open(path, *args, **kwargs):
            if path == '/proc/200/stat':
                raise PermissionError(13, 'denied')
            for pid, ppid in tree.items():
                if path == f'/proc/{pid}/stat':
                    return mock.mock_open(
                        read_data=self._stat_bytes(pid, ppid))(*args, **kwargs)
            raise FileNotFoundError(path)

        with mock.patch.object(subprocess_utils.os,
                               'listdir',
                               return_value=[str(p) for p in tree] + ['self']):
            with mock.patch.object(subprocess_utils, 'open', fake_open):
                # All real children have ctime >= parent's 100.0 so PID-reuse
                # defense doesn't filter anything out.
                with mock.patch.object(
                        subprocess_utils.psutil,
                        'Process',
                        side_effect=lambda p: self._fake_proc(p, 200.0)):
                    result = subprocess_utils._fallback_children(
                        100, parent_ctime=100.0)

        result_pids = sorted(p.pid for p in result)
        self.assertEqual(result_pids, [201])

    def test_pid_reuse_phantom_is_excluded(self):
        # Tree: 100 → {201, 202}. 201 has an *earlier* ctime than parent
        # 100 — it must be a recycled PID, not a real descendant, and
        # must NOT be returned. 202 is legit and must be returned.
        tree = {100: 1, 201: 100, 202: 100}

        def fake_open(path, *args, **kwargs):
            for pid, ppid in tree.items():
                if path == f'/proc/{pid}/stat':
                    return mock.mock_open(
                        read_data=self._stat_bytes(pid, ppid))(*args, **kwargs)
            raise FileNotFoundError(path)

        ctime = {201: 50.0, 202: 200.0}  # 201 predates parent (100.0)
        with mock.patch.object(subprocess_utils.os,
                               'listdir',
                               return_value=[str(p) for p in tree]):
            with mock.patch.object(subprocess_utils, 'open', fake_open):
                with mock.patch.object(subprocess_utils.psutil,
                                       'Process',
                                       side_effect=lambda p: self._fake_proc(
                                           p, ctime.get(p, 200.0))):
                    result = subprocess_utils._fallback_children(
                        100, parent_ctime=100.0)
        self.assertEqual([p.pid for p in result], [202])

    def test_non_linux_returns_empty(self):
        # If psutil already failed and we're not on Linux, there is no
        # better source — return [] rather than guessing.
        with mock.patch.object(subprocess_utils.sys, 'platform', 'darwin'):
            self.assertEqual(
                subprocess_utils._fallback_children(1, parent_ctime=0.0), [])


class TestKillProcessDaemonTolerates(unittest.TestCase):
    """Verify EACCES surfaced by _safe_children doesn't break
    kill_process_daemon (the path that broke managed jobs)."""

    def test_access_denied_does_not_raise(self):
        proc = subprocess.Popen(['sleep', '30'])
        try:
            with mock.patch.object(subprocess_utils,
                                   '_safe_children',
                                   return_value=[]):
                with mock.patch.object(subprocess_utils.subprocess, 'Popen'):
                    subprocess_utils.kill_process_daemon(proc.pid,
                                                         use_kill_pg=True)
        finally:
            proc.kill()
            proc.wait()


class TestKillChildrenProcessesTolerates(unittest.TestCase):
    """Verify the explicitly given parent is killed even when child
    enumeration returns empty (the contract callers rely on for cleanup
    correctness)."""

    def test_parent_still_killed_on_walk_failure(self):
        proc = subprocess.Popen(['sleep', '30'])
        try:
            with mock.patch.object(subprocess_utils,
                                   '_safe_children',
                                   return_value=[]):
                subprocess_utils.kill_children_processes([proc.pid], force=True)
            self.assertIsNotNone(proc.wait(timeout=5))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

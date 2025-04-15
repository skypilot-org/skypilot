"""Unit tests for subprocess_utils.py."""
import logging
import multiprocessing
import signal
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

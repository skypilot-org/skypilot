"""Unit tests for subprocess_utils.py."""
import multiprocessing
import signal
import time
import unittest

import psutil

from sky.utils import subprocess_utils


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
        subprocess_utils.kill_process_with_grace(process,
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
        subprocess_utils.kill_process_with_grace(process,
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
        subprocess_utils.kill_process_with_grace(psutil_proc,
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
        subprocess_utils.kill_process_with_grace(process,
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
        subprocess_utils.kill_process_with_grace(process,
                                                 force=False,
                                                 grace_period=1)

        # Process should be terminated by SIGKILL after grace period
        time.sleep(0.1)  # Give some time for the process to be fully terminated
        self.assertFalse(process.is_alive())


if __name__ == '__main__':
    unittest.main()

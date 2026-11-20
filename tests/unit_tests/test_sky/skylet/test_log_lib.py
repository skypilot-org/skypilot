"""Unit tests for skylet log_lib."""

from io import StringIO
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

from sky.skylet import log_lib

_HOLDER_SCRIPT = textwrap.dedent("""\
    import os
    import sys
    import time

    mode, pid_path = sys.argv[1:3]
    if os.fork() > 0:
        sys.exit(0)
    if mode == 'own-session':
        os.setsid()
    with open(pid_path, 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()))
    time.sleep(60)
    """)

_DRIVER_SCRIPT = textwrap.dedent("""\
    import os
    import signal
    import sys
    import threading
    import time

    import psutil

    from sky.skylet import log_lib

    mode, holder_path, pid_path, log_path = sys.argv[1:5]


    def _sigterm_handler(signum, frame):
        raise KeyboardInterrupt


    signal.signal(signal.SIGTERM, _sigterm_handler)


    def _cancel_when_holder_ready():
        deadline = time.time() + 30
        while not os.path.exists(pid_path) and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)


    threading.Thread(target=_cancel_when_holder_ready, daemon=True).start()
    try:
        log_lib.run_with_log(
            f'{sys.executable} {holder_path} {mode} {pid_path}; sleep 60',
            log_path,
            shell=True)
        print('returned', flush=True)
    except KeyboardInterrupt:
        with open(pid_path, encoding='utf-8') as f:
            holder_pid = int(f.read())
        deadline = time.time() + 5
        holder_dead = False
        while time.time() < deadline and not holder_dead:
            try:
                holder_dead = (psutil.Process(holder_pid).status() ==
                               psutil.STATUS_ZOMBIE)
            except psutil.NoSuchProcess:
                holder_dead = True
            if not holder_dead:
                time.sleep(0.1)
        print(f'interrupted holder_dead={holder_dead}', flush=True)
    rc = log_lib.run_with_log('echo ok', log_path, shell=True)
    print(f'reused rc={rc}', flush=True)
    """)


class TestLogBuffer(unittest.TestCase):
    """Test cases for LogBuffer class."""

    def test_initialization(self):
        """Test buffer initializes with correct defaults."""
        buffer = log_lib.LogBuffer()

        self.assertEqual(buffer.max_chars, log_lib.DEFAULT_LOG_CHUNK_SIZE)
        self.assertIsInstance(buffer._buffer, StringIO)
        self.assertEqual(buffer._buffer.getvalue(), '')

    def test_custom_parameters(self):
        """Test buffer initializes with custom parameters."""
        buffer = log_lib.LogBuffer(max_chars=1024)
        self.assertEqual(buffer.max_chars, 1024)

    def test_write_basic(self):
        """Test adding a single line to buffer."""
        buffer = log_lib.LogBuffer(max_chars=100)

        string = "Hello world\n"
        should_flush = buffer.write(string)

        self.assertFalse(should_flush)
        self.assertEqual(buffer._buffer.tell(), len(string))
        self.assertEqual(buffer._buffer.getvalue(), string)

    def test_write_triggers_size_flush(self):
        """Test that buffer flushes when size limit is reached."""
        buffer = log_lib.LogBuffer(max_chars=10)

        # Add a line that exceeds the size limit
        string = "This is a very long line that exceeds the buffer size\n"
        should_flush = buffer.write(string)

        self.assertTrue(should_flush)
        self.assertEqual(buffer._buffer.tell(), len(string))

    def test_flush_basic(self):
        """Test getting chunk from buffer."""
        buffer = log_lib.LogBuffer()

        buffer.write("Line 1\n")
        buffer.write("Line 2\n")
        buffer.write("Line 3\n")

        chunk = buffer.flush()

        self.assertEqual(chunk, "Line 1\nLine 2\nLine 3\n")
        self.assertEqual(buffer._buffer.tell(), 0)

    def test_flush_empty(self):
        """Test getting chunk from empty buffer."""
        buffer = log_lib.LogBuffer()

        chunk = buffer.flush()

        self.assertEqual(chunk, "")

    def test_unicode_characters(self):
        """Test buffer handles unicode characters correctly."""
        buffer = log_lib.LogBuffer()

        unicode_line = "Hello 🌍\n"
        buffer.write(unicode_line)

        # _buffer.tell() counts the number of characters,
        # not the number of bytes:
        # >>> len(unicode_line)
        # 8
        # >>> len(unicode_line.encode('utf-8'))
        # 11
        #
        # This is fine because our default chunk size is well below the
        # default grpc.max_receive_message_length which is 4MB.
        self.assertEqual(buffer._buffer.tell(), len(unicode_line))

        chunk = buffer.flush()
        self.assertEqual(chunk, unicode_line)

    def test_reset_after_flush(self):
        """Test that buffer is properly reset after getting chunk."""
        buffer = log_lib.LogBuffer()

        buffer.write("Line 1\n")
        buffer.write("Line 2\n")

        # Get chunk should reset everything
        chunk = buffer.flush()

        self.assertEqual(chunk, "Line 1\nLine 2\n")
        self.assertEqual(buffer._buffer.tell(), 0)


class TestRunWithLogTimeout(unittest.TestCase):
    """Test cases for run_with_log timeout functionality."""

    def test_process_stream_timeout_exceeded(self):
        """Test that timeout works with process_stream=True."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that sleeps longer than timeout
        cmd = ['sleep', '10']
        with self.assertRaises(subprocess.TimeoutExpired):
            log_lib.run_with_log(
                cmd,
                log_path,
                process_stream=True,
                timeout=1,
            )

    def test_process_stream_timeout_not_exceeded(self):
        """Test normal completion with process_stream=True and timeout set."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that completes quickly
        cmd = ['echo', 'hello']
        returncode = log_lib.run_with_log(
            cmd,
            log_path,
            process_stream=True,
            timeout=10,
        )
        self.assertEqual(returncode, 0)

    def test_no_stream_timeout_exceeded(self):
        """Test that timeout works with process_stream=False."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that sleeps longer than timeout
        cmd = ['sleep', '10']
        with self.assertRaises(subprocess.TimeoutExpired):
            log_lib.run_with_log(
                cmd,
                log_path,
                process_stream=False,
                timeout=1,
            )

    def test_no_stream_timeout_not_exceeded(self):
        """Test normal completion with process_stream=False and timeout set."""
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
            log_path = f.name

        # Command that completes quickly
        cmd = ['echo', 'hello']
        returncode = log_lib.run_with_log(
            cmd,
            log_path,
            process_stream=False,
            timeout=10,
        )
        self.assertEqual(returncode, 0)


class TestRunWithLogKeyboardInterrupt(unittest.TestCase):
    """run_with_log must not wedge when a cancelled command's subprocess
    leaves a process holding its stderr pipe outside the process tree."""

    def _run_cancelled_driver(self, mode):
        with tempfile.TemporaryDirectory() as tmp_dir:
            holder_path = os.path.join(tmp_dir, 'holder.py')
            driver_path = os.path.join(tmp_dir, 'driver.py')
            pid_path = os.path.join(tmp_dir, 'holder.pid')
            log_path = os.path.join(tmp_dir, 'run.log')
            with open(holder_path, 'w', encoding='utf-8') as f:
                f.write(_HOLDER_SCRIPT)
            with open(driver_path, 'w', encoding='utf-8') as f:
                f.write(_DRIVER_SCRIPT)
            env = dict(os.environ)
            repo_root = str(pathlib.Path(log_lib.__file__).resolve().parents[2])
            env['PYTHONPATH'] = os.pathsep.join(
                [repo_root, env.get('PYTHONPATH', '')])
            try:
                return subprocess.run([
                    sys.executable, driver_path, mode, holder_path, pid_path,
                    log_path
                ],
                                      capture_output=True,
                                      text=True,
                                      timeout=60,
                                      env=env,
                                      check=False)
            finally:
                try:
                    with open(pid_path, encoding='utf-8') as f:
                        os.kill(int(f.read()), signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError, ValueError):
                    pass

    def test_interrupt_kills_process_group_holder(self):
        result = self._run_cancelled_driver('group')
        self.assertIn('interrupted holder_dead=True', result.stdout,
                      result.stderr[-2000:])
        self.assertIn('reused rc=0', result.stdout, result.stderr[-2000:])

    def test_interrupt_returns_with_detached_holder(self):
        result = self._run_cancelled_driver('own-session')
        self.assertIn('interrupted holder_dead=False', result.stdout,
                      result.stderr[-2000:])
        self.assertIn('reused rc=0', result.stdout, result.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()

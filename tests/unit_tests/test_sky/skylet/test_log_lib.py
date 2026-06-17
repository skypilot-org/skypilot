"""Unit tests for skylet log_lib."""

from io import StringIO
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from sky.skylet import log_lib
from sky.utils import context
from sky.utils import subprocess_utils


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


class TestRunWithLogPreexecGating(unittest.TestCase):
    """Tests that run_with_log gates the subreaper preexec_fn correctly.

    Passing a Python preexec_fn to subprocess.Popen forces CPython onto its
    unsafe multi-threaded fork path (it disables the vfork/posix_spawn fast
    paths). On the highly concurrent API server (coroutine path, ctx is not
    None) this deadlocks: one worker can fork() while another holds a glibc
    allocator lock, and the forked child wedges before execve() while the
    parent blocks forever in Popen._execute_child. The coroutine path reaps
    descendants via the process group (kill_process_daemon(use_kill_pg=True))
    and so must NOT set the subreaper preexec; the cluster/task path (ctx is
    None) still needs it for orphan cleanup.

    This is the regression guard for the API-server hang reproduced on CI
    (test_high_logs_concurrency_not_blocking_operations): it fails on the
    pre-fix code (preexec set unconditionally) and passes once preexec is
    gated to the ctx-is-None path.
    """

    def _capture_command_preexec(self, server_path: bool):
        """Returns the preexec_fn run_with_log hands to the command's Popen.

        Runs in a fresh thread so the contextvar state is isolated: ContextVars
        are not inherited across threads, so the thread starts with ctx=None and
        we opt into the server path by calling context.initialize() inside it.
        """
        cmd = ['true']
        captured = {}
        result = {}
        real_popen = subprocess.Popen

        class _SpyPopen(real_popen):

            def __init__(self, *args, **kwargs):
                popen_cmd = args[0] if args else kwargs.get('args')
                if popen_cmd == cmd:
                    captured['preexec_fn'] = kwargs.get('preexec_fn')
                # Neuter the subreaper preexec so this test process is never
                # itself turned into a child subreaper.
                kwargs['preexec_fn'] = None
                super().__init__(*args, **kwargs)

        def run():
            if server_path:
                context.initialize()
                assert context.get() is not None
            else:
                assert context.get() is None
            with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
                log_path = f.name
            # Skip the real reaper daemon; only the command's Popen matters.
            with mock.patch.object(subprocess_utils, 'kill_process_daemon'):
                with mock.patch('subprocess.Popen', _SpyPopen):
                    log_lib.run_with_log(cmd,
                                         log_path,
                                         stream_logs=False,
                                         process_stream=False)
            result['preexec'] = captured.get('preexec_fn', 'NOT_CALLED')

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=30)
        self.assertFalse(t.is_alive(), 'run_with_log did not return in time')
        self.assertIn('preexec', result, 'command Popen was never invoked')
        self.assertNotEqual(result['preexec'], 'NOT_CALLED')
        return result['preexec']

    def test_no_preexec_on_server_path(self):
        """On the coroutine/server path, preexec_fn must be None."""
        preexec = self._capture_command_preexec(server_path=True)
        self.assertIsNone(
            preexec,
            'run_with_log must not pass a preexec_fn on the coroutine path; a '
            'Python preexec_fn forces the unsafe multi-threaded fork and '
            'deadlocks the concurrent API server.')

    def test_preexec_set_on_cluster_path(self):
        """On the cluster/task path, the subreaper preexec must be set."""
        preexec = self._capture_command_preexec(server_path=False)
        self.assertTrue(
            callable(preexec),
            'run_with_log must set the subreaper preexec_fn on the cluster '
            'path to keep orphan reaping working.')


if __name__ == '__main__':
    unittest.main()

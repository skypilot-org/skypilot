"""Unit tests for skylet log_lib."""

from io import StringIO
import os
import subprocess
import sys
import tempfile
import tracemalloc
from typing import List
import unittest
from unittest import mock

from sky.skylet import log_lib
from sky.utils import context


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


_LINE_BYTES = 100


def _lines(prefix: str, count: int) -> List[str]:
    body = 'x' * (_LINE_BYTES - len(prefix) - 9)
    return [f'{prefix}{i:08d}{body}\n' for i in range(count)]


def _emit_cmd(stdout_lines: int,
              stderr_lines: int = 0,
              exit_code: int = 0) -> List[str]:
    """A child process that prints the lines `_lines` would build."""
    script = (f'import sys\n'
              f'body_out = "x" * {_LINE_BYTES - len("out") - 9}\n'
              f'body_err = "x" * {_LINE_BYTES - len("err") - 9}\n'
              f'for i in range({stdout_lines}):\n'
              f'    sys.stdout.write(f"out{{i:08d}}{{body_out}}\\n")\n'
              f'sys.stdout.flush()\n'
              f'for i in range({stderr_lines}):\n'
              f'    sys.stderr.write(f"err{{i:08d}}{{body_err}}\\n")\n'
              f'sys.stderr.flush()\n'
              f'sys.exit({exit_code})\n')
    return [sys.executable, '-c', script]


@context.contextual
def _run_with_log_in_context(*args, **kwargs):
    return log_lib.run_with_log(*args, **kwargs)


class TestRunWithLogOutputCapture(unittest.TestCase):
    """What run_with_log returns, logs, and keeps in memory."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self._tmp_dir.name, 'run.log')

    def tearDown(self):
        self._tmp_dir.cleanup()

    def _read_log(self) -> str:
        with open(self.log_path, 'r', encoding='utf-8') as f:
            return f.read()

    def test_require_outputs_returns_full_stdout_and_stderr(self):
        returncode, stdout, stderr = log_lib.run_with_log(_emit_cmd(1000, 500),
                                                          self.log_path,
                                                          require_outputs=True)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, ''.join(_lines('out', 1000)))
        self.assertEqual(stderr, ''.join(_lines('err', 500)))

    def test_require_outputs_with_ray_merges_stderr_into_stdout(self):
        returncode, stdout, stderr = log_lib.run_with_log(_emit_cmd(1000, 500),
                                                          self.log_path,
                                                          require_outputs=True,
                                                          with_ray=True)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout,
                         ''.join(_lines('out', 1000) + _lines('err', 500)))
        self.assertEqual(stderr, '')

    def test_require_outputs_in_context_returns_full_stdout_and_stderr(self):
        returncode, stdout, stderr = _run_with_log_in_context(
            _emit_cmd(1000, 500), self.log_path, require_outputs=True)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, ''.join(_lines('out', 1000)))
        self.assertEqual(stderr, ''.join(_lines('err', 500)))

    def test_no_outputs_logs_everything_and_keeps_exit_code(self):
        returncode = log_lib.run_with_log(_emit_cmd(1000, 500, exit_code=3),
                                          self.log_path,
                                          with_ray=True)
        self.assertEqual(returncode, 3)
        self.assertEqual(self._read_log(),
                         ''.join(_lines('out', 1000) + _lines('err', 500)))

    def test_no_outputs_in_context_logs_everything_and_keeps_exit_code(self):
        returncode = _run_with_log_in_context(_emit_cmd(1000, 500, exit_code=3),
                                              self.log_path,
                                              with_ray=True)
        self.assertEqual(returncode, 3)
        self.assertEqual(self._read_log(),
                         ''.join(_lines('out', 1000) + _lines('err', 500)))

    def test_no_outputs_still_streams_every_line(self):
        expected = ''.join(_lines('out', 1000) + _lines('err', 500))
        with mock.patch.object(sys, 'stdout', new=StringIO()) as streamed:
            returncode = log_lib.run_with_log(_emit_cmd(1000, 500, exit_code=3),
                                              self.log_path,
                                              stream_logs=True,
                                              with_ray=True)
        self.assertEqual(returncode, 3)
        self.assertEqual(streamed.getvalue(), expected)
        self.assertEqual(self._read_log(), expected)

    def _peak_traced_bytes(self, run, num_lines: int) -> int:
        tracemalloc.start()
        try:
            run(_emit_cmd(num_lines), self.log_path, with_ray=True)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak

    def _assert_memory_flat_in_output_size(self, run) -> None:
        small_lines, large_lines = 10_000, 40_000
        small_peak = self._peak_traced_bytes(run, small_lines)
        large_peak = self._peak_traced_bytes(run, large_lines)
        extra_output = (large_lines - small_lines) * _LINE_BYTES
        growth = large_peak - small_peak
        # Retaining the output grows the peak by more than the extra output
        # itself; draining it line by line keeps the peak flat.
        self.assertLess(
            growth, extra_output // 10,
            f'peak traced memory {small_peak:,} B for '
            f'{small_lines * _LINE_BYTES:,} B of output, {large_peak:,} B for '
            f'{large_lines * _LINE_BYTES:,} B of output: grew {growth:,} B '
            f'for {extra_output:,} B more output.')

    def test_no_outputs_does_not_retain_output(self):
        self._assert_memory_flat_in_output_size(log_lib.run_with_log)

    def test_no_outputs_in_context_does_not_retain_output(self):
        self._assert_memory_flat_in_output_size(_run_with_log_in_context)


if __name__ == '__main__':
    unittest.main()

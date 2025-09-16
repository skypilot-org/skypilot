"""Unit tests for skylet services."""

from io import StringIO
import math
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from sky.exceptions import JobExitCode
from sky.schemas.generated import jobsv1_pb2
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.skylet import services
from sky.utils import ux_utils


class TestTailLogsBuffering(unittest.TestCase):

    def setUp(self):
        self.service = services.JobsServiceImpl()
        self.initial_thread_count = threading.active_count()

    def test_successful_job(self):
        """Test reading mocked logs of a finished job.

        Because the log file is already finished, we should see that size-based
        flushing is invoked, because there shouldn't be any delays between each
        line emitted such that we hit the default time limit of 50ms.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, 'run.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                # Header must be present else we won't start streaming.
                f.write(f"{log_lib.LOG_FILE_START_STREAMING_AT}\n")
                # Write around 48KB of data
                for i in range(10000):
                    f.write(f'{i}\n')
                f.flush()
            file_size = os.stat(log_path).st_size

            # Sanity check
            self.assertGreaterEqual(file_size, 48000)
            num_chunks = math.ceil(file_size / log_lib.DEFAULT_LOG_CHUNK_SIZE)
            self.assertEqual(num_chunks, 3)

            with mock.patch('sky.skylet.services.job_lib.get_log_dir_for_job', return_value=tmpdir), \
                 mock.patch('sky.skylet.services.job_lib.update_job_status', return_value=[job_lib.JobStatus.SUCCEEDED]), \
                 mock.patch('sky.skylet.services.job_lib.get_status', return_value=job_lib.JobStatus.SUCCEEDED):

                req = jobsv1_pb2.TailLogsRequest(job_id=1, follow=False)
                responses = list(self.service.TailLogs(req, object()))

        chunks = [r.log_line for r in responses if r.log_line]
        self.assertEqual(len(chunks), num_chunks)
        for chunk in chunks:
            # Actual chunk sizes:
            # [16386, 16385, 16203]
            self.assertGreaterEqual(len(chunk), 16000)

        # First chunk
        self.assertEqual(
            chunks[0].startswith(
                f'{log_lib.LOG_FILE_START_STREAMING_AT}\n0\n1\n'), True)
        last_in_first = int(chunks[0].strip().split('\n')[-1])
        # Second chunk
        self.assertEqual(
            chunks[1].startswith(f'{last_in_first + 1}\n{last_in_first + 2}\n'),
            True)
        last_in_second = int(chunks[1].strip().split('\n')[-1])
        # Last chunk
        self.assertEqual(
            chunks[2].startswith(
                f'{last_in_second + 1}\n{last_in_second + 2}\n'), True)
        finish = ux_utils.finishing_message('Job finished (status: SUCCEEDED).')
        self.assertTrue(chunks[-1].endswith(f'9998\n9999\n{finish}\n'))
        final_exit_code = responses[-1].exit_code
        self.assertEqual(final_exit_code, JobExitCode.SUCCEEDED)

        # Verify no thread leaks
        self.assertEqual(threading.active_count(), self.initial_thread_count)

    def test_in_progress_job(self):
        """Test reading mocked logs of an in-progress job.

        Because the log file is still being written to, we should see that
        time-based flushing is invoked, which we mocked by sleeping for
        longer than the default time limit of 50ms between some log lines.
        """

        def iterator_with_delay(*_args, **_kwargs):
            yield 'a\n'
            # Force flush timeout
            time.sleep(services.DEFAULT_LOG_CHUNK_FLUSH_INTERVAL + 0.01)
            yield 'b\n'
            yield 'c\n'
            # Force flush timeout
            time.sleep(services.DEFAULT_LOG_CHUNK_FLUSH_INTERVAL + 0.01)
            yield 'd\n'

        with mock.patch('sky.skylet.services.job_lib.get_log_dir_for_job', return_value='/tmp'), \
             mock.patch('sky.skylet.services.job_lib.get_status', return_value=job_lib.JobStatus.RUNNING), \
             mock.patch('sky.skylet.services.log_lib.tail_logs_iter', side_effect=iterator_with_delay):

            req = jobsv1_pb2.TailLogsRequest(job_id=2, follow=True, tail=0)
            responses = list(self.service.TailLogs(req, object()))

        chunks = [r.log_line for r in responses if r.log_line]
        # Expect two time-triggered chunks combining lines around delays.
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], 'a\n')
        self.assertEqual(chunks[1], 'b\nc\n')
        self.assertEqual(chunks[2], 'd\n')

        # For follow=True and RUNNING status, exit code should be NOT_FINISHED.
        self.assertEqual(responses[-1].exit_code, JobExitCode.NOT_FINISHED)

        # Verify no thread leaks
        self.assertEqual(threading.active_count(), self.initial_thread_count)


class TestLogChunkBuffer(unittest.TestCase):
    """Test cases for LogChunkBuffer class."""

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

        #.tell() counts the number of characters,
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


if __name__ == '__main__':
    unittest.main()

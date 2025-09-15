"""Unit tests for skylet services."""

from io import StringIO
import sys
import time
import unittest

from sky.skylet.services import LogChunkBuffer


class TestLogChunkBuffer(unittest.TestCase):
    """Test cases for LogChunkBuffer class."""

    def test_buffer_initialization(self):
        """Test buffer initializes with correct defaults."""
        buffer = LogChunkBuffer()

        self.assertEqual(buffer.max_size, 32 * 1024)
        self.assertEqual(buffer.flush_interval, 0.01)
        self.assertIsInstance(buffer._buffer, StringIO)
        self.assertEqual(buffer._buffer.getvalue(), '')
        self.assertIsInstance(buffer.last_flush, float)

    def test_buffer_custom_parameters(self):
        """Test buffer initializes with custom parameters."""
        buffer = LogChunkBuffer(max_size=1024, flush_interval=1.0)

        self.assertEqual(buffer.max_size, 1024)
        self.assertEqual(buffer.flush_interval, 1.0)

    def test_write_basic(self):
        """Test adding a single line to buffer."""
        buffer = LogChunkBuffer(max_size=100, flush_interval=10.0)

        string = "Hello world\n"
        should_flush = buffer.write(string)

        self.assertFalse(should_flush)
        self.assertEqual(buffer._buffer.tell(), len(string))
        self.assertEqual(buffer._buffer.getvalue(), string)

    def test_write_triggers_size_flush(self):
        """Test that buffer flushes when size limit is reached."""
        buffer = LogChunkBuffer(max_size=10, flush_interval=10.0)

        # Add a line that exceeds the size limit
        string = "This is a very long line that exceeds the buffer size\n"
        should_flush = buffer.write(string)

        self.assertTrue(should_flush)
        self.assertEqual(buffer._buffer.tell(), len(string))

    def test_write_triggers_time_flush(self):
        """Test that buffer flushes when time limit is reached."""
        buffer = LogChunkBuffer(max_size=1000,
                                flush_interval=0.01)  # Very short interval

        # Add a line
        should_flush = buffer.write("Short line\n")
        self.assertFalse(should_flush)

        # Wait for time interval to pass
        time.sleep(0.02)

        # Check if should_flush returns True now
        self.assertTrue(buffer._should_flush())

    def test_write_triggers_time_flush(self):
        """Test that buffer flushes when time limit is reached."""
        buffer = LogChunkBuffer(max_size=1000,
                                flush_interval=0.01)  # Very short interval

        # Add a line
        should_flush = buffer.write("Short line\n")
        self.assertFalse(should_flush)

        # Wait for time interval to pass
        time.sleep(0.02)

        # Check if should_flush returns True now
        self.assertTrue(buffer._should_flush())

    def test_write_triggers_time_flush_longer_interval(self):
        """Test that buffer flushes when time limit is reached."""
        buffer = LogChunkBuffer(max_size=1000, flush_interval=5)

        # Add a line
        should_flush = buffer.write("Short line\n")
        self.assertFalse(should_flush)

        # Wait for a short time
        time.sleep(1)
        self.assertFalse(buffer._should_flush())

        # Wait for the interval to pass
        time.sleep(5)
        self.assertTrue(buffer._should_flush())

    def test_flush_basic(self):
        """Test getting chunk from buffer."""
        buffer = LogChunkBuffer()

        buffer.write("Line 1\n")
        buffer.write("Line 2\n")
        buffer.write("Line 3\n")

        chunk = buffer.flush()

        self.assertEqual(chunk, "Line 1\nLine 2\nLine 3\n")
        self.assertEqual(buffer._buffer.tell(), 0)

    def test_flush_empty_buffer(self):
        """Test getting chunk from empty buffer."""
        buffer = LogChunkBuffer()

        chunk = buffer.flush()

        self.assertEqual(chunk, "")

    def test_empty(self):
        """Test empty method."""
        buffer = LogChunkBuffer()

        # Empty buffer
        self.assertEqual(buffer._buffer.tell(), 0)

        # Buffer with content
        buffer.write("Some content\n")
        self.assertGreater(buffer._buffer.tell(), 0)

        # After getting chunk (empty again)
        buffer.flush()
        self.assertEqual(buffer._buffer.tell(), 0)

    def test_multiple_lines_size_calculation(self):
        """Test that buffer size is calculated correctly for multiple lines."""
        buffer = LogChunkBuffer()

        line1 = "First line\n"
        line2 = "Second line\n"
        line3 = "Third line\n"

        buffer.write(line1)
        expected_size = len(line1.encode('utf-8'))
        self.assertEqual(buffer._buffer.tell(), expected_size)

        buffer.write(line2)
        expected_size += len(line2.encode('utf-8'))
        self.assertEqual(buffer._buffer.tell(), expected_size)

        buffer.write(line3)
        expected_size += len(line3.encode('utf-8'))
        self.assertEqual(buffer._buffer.tell(), expected_size)

    def test_unicode_characters(self):
        """Test buffer handles unicode characters correctly."""
        buffer = LogChunkBuffer()

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

    def test_buffer_reset_after_flush(self):
        """Test that buffer is properly reset after getting chunk."""
        buffer = LogChunkBuffer()

        buffer.write("Line 1\n")
        buffer.write("Line 2\n")

        # Get chunk should reset everything
        chunk = buffer.flush()

        self.assertEqual(chunk, "Line 1\nLine 2\n")
        self.assertEqual(buffer._buffer.tell(), 0)

        # last_flush should be updated
        old_flush_time = buffer.last_flush
        time.sleep(0.001)
        buffer.flush()  # Should update last_flush even if empty
        self.assertGreaterEqual(buffer.last_flush, old_flush_time)


if __name__ == '__main__':
    unittest.main()

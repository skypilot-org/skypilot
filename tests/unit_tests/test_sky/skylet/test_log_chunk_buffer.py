"""Unit tests for skylet services."""

import time
import unittest

from sky.skylet.services import LogChunkBuffer


class TestLogChunkBuffer(unittest.TestCase):
    """Test cases for LogChunkBuffer class."""

    def test_buffer_initialization(self):
        """Test buffer initializes with correct defaults."""
        buffer = LogChunkBuffer()

        self.assertEqual(buffer.max_size, 4096)
        self.assertEqual(buffer.flush_interval, 0.5)
        self.assertEqual(buffer.buffer, [])
        self.assertEqual(buffer.buffer_size, 0)
        self.assertIsInstance(buffer.last_flush, float)

    def test_buffer_custom_parameters(self):
        """Test buffer initializes with custom parameters."""
        buffer = LogChunkBuffer(max_size=1024, flush_interval=1.0)

        self.assertEqual(buffer.max_size, 1024)
        self.assertEqual(buffer.flush_interval, 1.0)

    def test_add_line_basic(self):
        """Test adding a single line to buffer."""
        buffer = LogChunkBuffer(max_size=100, flush_interval=10.0)

        should_flush = buffer.add_line("Hello world\n")

        self.assertFalse(should_flush)
        self.assertEqual(len(buffer.buffer), 1)
        self.assertEqual(buffer.buffer[0], "Hello world\n")
        self.assertEqual(buffer.buffer_size,
                         len("Hello world\n".encode('utf-8')))

    def test_add_line_triggers_size_flush(self):
        """Test that buffer flushes when size limit is reached."""
        buffer = LogChunkBuffer(max_size=10, flush_interval=10.0)

        # Add a line that exceeds the size limit
        should_flush = buffer.add_line(
            "This is a very long line that exceeds the buffer size\n")

        self.assertTrue(should_flush)
        self.assertEqual(len(buffer.buffer), 1)
        self.assertGreater(buffer.buffer_size, 10)

    def test_add_line_triggers_time_flush(self):
        """Test that buffer flushes when time limit is reached."""
        buffer = LogChunkBuffer(max_size=1000,
                                flush_interval=0.01)  # Very short interval

        # Add a line
        should_flush = buffer.add_line("Short line\n")
        self.assertFalse(should_flush)

        # Wait for time interval to pass
        time.sleep(0.02)

        # Check if should_flush returns True now
        self.assertTrue(buffer.should_flush())

    def test_add_line_triggers_time_flush(self):
        """Test that buffer flushes when time limit is reached."""
        buffer = LogChunkBuffer(max_size=1000,
                                flush_interval=0.01)  # Very short interval

        # Add a line
        should_flush = buffer.add_line("Short line\n")
        self.assertFalse(should_flush)

        # Wait for time interval to pass
        time.sleep(0.02)

        # Check if should_flush returns True now
        self.assertTrue(buffer.should_flush())

    def test_add_line_triggers_time_flush_longer_interval(self):
        """Test that buffer flushes when time limit is reached."""
        buffer = LogChunkBuffer(max_size=1000, flush_interval=5)

        # Add a line
        should_flush = buffer.add_line("Short line\n")
        self.assertFalse(should_flush)

        # Wait for a short time
        time.sleep(1)
        self.assertFalse(buffer.should_flush())

        # Wait for the interval to pass
        time.sleep(5)
        self.assertTrue(buffer.should_flush())

    def test_get_chunk_basic(self):
        """Test getting chunk from buffer."""
        buffer = LogChunkBuffer()

        buffer.add_line("Line 1\n")
        buffer.add_line("Line 2\n")
        buffer.add_line("Line 3\n")

        chunk = buffer.get_chunk()

        self.assertEqual(chunk, "Line 1\nLine 2\nLine 3\n")
        self.assertEqual(len(buffer.buffer), 0)
        self.assertEqual(buffer.buffer_size, 0)

    def test_get_chunk_empty_buffer(self):
        """Test getting chunk from empty buffer."""
        buffer = LogChunkBuffer()

        chunk = buffer.get_chunk()

        self.assertEqual(chunk, "")

    def test_empty(self):
        """Test empty method."""
        buffer = LogChunkBuffer()

        # Empty buffer
        self.assertTrue(buffer.empty())

        # Buffer with content
        buffer.add_line("Some content\n")
        self.assertFalse(buffer.empty())

        # After getting chunk (empty again)
        buffer.get_chunk()
        self.assertTrue(buffer.empty())

    def test_multiple_lines_size_calculation(self):
        """Test that buffer size is calculated correctly for multiple lines."""
        buffer = LogChunkBuffer()

        line1 = "First line\n"
        line2 = "Second line\n"
        line3 = "Third line\n"

        buffer.add_line(line1)
        expected_size = len(line1.encode('utf-8'))
        self.assertEqual(buffer.buffer_size, expected_size)

        buffer.add_line(line2)
        expected_size += len(line2.encode('utf-8'))
        self.assertEqual(buffer.buffer_size, expected_size)

        buffer.add_line(line3)
        expected_size += len(line3.encode('utf-8'))
        self.assertEqual(buffer.buffer_size, expected_size)

    def test_unicode_characters(self):
        """Test buffer handles unicode characters correctly."""
        buffer = LogChunkBuffer()

        unicode_line = "Hello 🌍\n"
        buffer.add_line(unicode_line)

        # Unicode characters should be counted by their UTF-8 byte size
        # "Hello " -> 6 bytes
        # "🌍" -> 4 bytes
        # "\n" -> 1 byte
        expected_size = 6 + 4 + 1
        self.assertEqual(buffer.buffer_size, expected_size)

        chunk = buffer.get_chunk()
        self.assertEqual(chunk, unicode_line)

    def test_buffer_reset_after_get_chunk(self):
        """Test that buffer is properly reset after getting chunk."""
        buffer = LogChunkBuffer()

        buffer.add_line("Line 1\n")
        buffer.add_line("Line 2\n")

        # Get chunk should reset everything
        chunk = buffer.get_chunk()

        self.assertEqual(chunk, "Line 1\nLine 2\n")
        self.assertEqual(len(buffer.buffer), 0)
        self.assertEqual(buffer.buffer_size, 0)
        self.assertTrue(buffer.empty())

        # last_flush should be updated
        old_flush_time = buffer.last_flush
        time.sleep(0.001)
        buffer.get_chunk()  # Should update last_flush even if empty
        self.assertGreaterEqual(buffer.last_flush, old_flush_time)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for skylet log_lib."""

from io import StringIO
import unittest

from sky.skylet import log_lib


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


if __name__ == '__main__':
    unittest.main()

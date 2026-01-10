"""Unit tests for sky/server/stream_utils.py."""

import asyncio
import io
import pathlib
import tempfile
from unittest import mock

import fastapi
import pytest

from sky.server import stream_utils
from sky.server.requests import requests as requests_lib


class TestStreamResponseConstants:
    """Tests for stream_utils constants."""

    def test_buffer_size_is_reasonable(self):
        """Test that buffer size is a reasonable value."""
        assert stream_utils._BUFFER_SIZE == 8 * 1024  # 8KB
        assert stream_utils._BUFFER_SIZE > 0

    def test_buffer_timeout_is_short(self):
        """Test that buffer timeout is short for responsiveness."""
        assert stream_utils._BUFFER_TIMEOUT == 0.02  # 20ms
        assert stream_utils._BUFFER_TIMEOUT < 1.0

    def test_heartbeat_interval_is_reasonable(self):
        """Test that heartbeat interval is reasonable."""
        assert stream_utils._HEARTBEAT_INTERVAL == 30  # 30 seconds

    def test_read_chunk_size_is_reasonable(self):
        """Test that read chunk size is reasonable."""
        assert stream_utils._READ_CHUNK_SIZE == 256 * 1024  # 256KB

    def test_poll_intervals_are_defined(self):
        """Test that poll intervals are defined."""
        assert stream_utils.LONG_REQUEST_POLL_INTERVAL == 1
        assert stream_utils.DEFAULT_POLL_INTERVAL == 0.1


class TestStreamResponse:
    """Tests for stream_response() function."""

    def test_creates_streaming_response(self):
        """Test that stream_response creates a StreamingResponse."""
        with mock.patch.object(stream_utils, 'log_streamer') as mock_streamer:
            mock_streamer.return_value = iter([])

            background_tasks = fastapi.BackgroundTasks()
            response = stream_utils.stream_response(
                request_id='test-request',
                logs_path=pathlib.Path('/tmp/test.log'),
                background_tasks=background_tasks)

            assert isinstance(response, fastapi.responses.StreamingResponse)
            assert response.media_type == 'text/plain'

    def test_response_has_cache_control_headers(self):
        """Test that response has proper cache control headers."""
        with mock.patch.object(stream_utils, 'log_streamer') as mock_streamer:
            mock_streamer.return_value = iter([])

            background_tasks = fastapi.BackgroundTasks()
            response = stream_utils.stream_response(
                request_id='test-request',
                logs_path=pathlib.Path('/tmp/test.log'),
                background_tasks=background_tasks)

            assert 'no-cache' in response.headers.get('Cache-Control', '')
            assert response.headers.get('X-Accel-Buffering') == 'no'
            assert response.headers.get('Transfer-Encoding') == 'chunked'

    def test_adds_disconnect_handler_when_enabled(self):
        """Test that disconnect handler is added when kill_request_on_disconnect is True."""
        with mock.patch.object(stream_utils, 'log_streamer') as mock_streamer:
            mock_streamer.return_value = iter([])

            background_tasks = fastapi.BackgroundTasks()
            stream_utils.stream_response(
                request_id='test-request',
                logs_path=pathlib.Path('/tmp/test.log'),
                background_tasks=background_tasks,
                kill_request_on_disconnect=True)

            # Should have added a task
            assert len(background_tasks.tasks) == 1

    def test_no_disconnect_handler_when_disabled(self):
        """Test that no disconnect handler when kill_request_on_disconnect is False."""
        with mock.patch.object(stream_utils, 'log_streamer') as mock_streamer:
            mock_streamer.return_value = iter([])

            background_tasks = fastapi.BackgroundTasks()
            stream_utils.stream_response(
                request_id='test-request',
                logs_path=pathlib.Path('/tmp/test.log'),
                background_tasks=background_tasks,
                kill_request_on_disconnect=False)

            # Should not have added any tasks
            assert len(background_tasks.tasks) == 0


class TestStreamResponseForLongRequest:
    """Tests for stream_response_for_long_request() function."""

    def test_uses_long_request_poll_interval(self):
        """Test that long request uses LONG_REQUEST_POLL_INTERVAL."""
        with mock.patch.object(stream_utils, 'stream_response') as mock_stream:
            background_tasks = fastapi.BackgroundTasks()
            stream_utils.stream_response_for_long_request(
                request_id='test-request',
                logs_path=pathlib.Path('/tmp/test.log'),
                background_tasks=background_tasks)

            mock_stream.assert_called_once()
            call_kwargs = mock_stream.call_args[1]
            assert call_kwargs[
                'polling_interval'] == stream_utils.LONG_REQUEST_POLL_INTERVAL


class TestLogStreamer:
    """Tests for log_streamer() async generator."""

    @pytest.fixture
    def temp_log_file(self, tmp_path):
        """Create a temporary log file."""
        log_file = tmp_path / 'test.log'
        log_file.write_text('Line 1\nLine 2\nLine 3\n')
        return log_file

    @pytest.mark.asyncio
    async def test_streams_log_file_content(self, temp_log_file):
        """Test that log file content is streamed."""
        chunks = []

        async for chunk in stream_utils.log_streamer(request_id=None,
                                                     log_path=temp_log_file,
                                                     follow=False):
            chunks.append(chunk)

        combined = ''.join(chunks)
        assert 'Line 1' in combined
        assert 'Line 2' in combined
        assert 'Line 3' in combined

    @pytest.mark.asyncio
    async def test_raises_on_missing_request(self):
        """Test that missing request raises HTTPException."""
        with mock.patch('sky.server.requests.requests.get_request_async',
                        return_value=None):
            with pytest.raises(fastapi.HTTPException) as exc_info:
                async for _ in stream_utils.log_streamer(
                        request_id='non-existent',
                        log_path=pathlib.Path('/tmp/test.log'),
                        follow=False):
                    pass

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_streams_directory_of_logs(self, tmp_path):
        """Test streaming all log files in a directory."""
        # Create multiple log files
        (tmp_path / 'log1.log').write_text('Log 1 content\n')
        (tmp_path / 'log2.log').write_text('Log 2 content\n')

        chunks = []
        async for chunk in stream_utils.log_streamer(request_id=None,
                                                     log_path=tmp_path,
                                                     follow=False):
            chunks.append(chunk)

        combined = ''.join(chunks)
        assert 'Log 1 content' in combined
        assert 'Log 2 content' in combined
        # Should have file headers
        assert '==>' in combined

    @pytest.mark.asyncio
    async def test_handles_tail_parameter(self, tmp_path):
        """Test tailing specific number of lines."""
        log_file = tmp_path / 'test.log'
        log_file.write_text('Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n')

        chunks = []
        async for chunk in stream_utils.log_streamer(request_id=None,
                                                     log_path=log_file,
                                                     tail=2,
                                                     follow=False):
            chunks.append(chunk)

        combined = ''.join(chunks)
        # Should only have last 2 lines
        assert 'Line 4' in combined
        assert 'Line 5' in combined
        # First lines should not be present
        assert 'Line 1' not in combined


class TestTailLogFile:
    """Tests for _tail_log_file() async generator."""

    @pytest.fixture
    def temp_log_file(self, tmp_path):
        """Create a temporary log file."""
        log_file = tmp_path / 'test.log'
        log_file.write_text('Test line 1\nTest line 2\n')
        return log_file

    @pytest.mark.asyncio
    async def test_reads_existing_content(self, temp_log_file):
        """Test that existing log content is read."""
        import aiofiles

        chunks = []
        async with aiofiles.open(temp_log_file, 'rb') as f:
            async for chunk in stream_utils._tail_log_file(f, follow=False):
                chunks.append(chunk)

        combined = ''.join(chunks)
        assert 'Test line 1' in combined
        assert 'Test line 2' in combined

    @pytest.mark.asyncio
    async def test_filters_payload_lines_in_plain_mode(self, tmp_path):
        """Test that payload lines are filtered in plain_logs mode."""
        from sky.utils import message_utils

        # Create log with mixed content
        log_file = tmp_path / 'test.log'
        payload = message_utils.encode_payload('hidden data')
        log_file.write_text(f'Visible line\n{payload}\nAnother visible\n')

        import aiofiles
        chunks = []
        async with aiofiles.open(log_file, 'rb') as f:
            async for chunk in stream_utils._tail_log_file(f,
                                                           plain_logs=True,
                                                           follow=False):
                chunks.append(chunk)

        combined = ''.join(chunks)
        assert 'Visible line' in combined
        assert 'Another visible' in combined
        # Payload should be filtered
        assert 'hidden data' not in combined


class TestYieldLogFileWithPayloadsSkipped:
    """Tests for _yield_log_file_with_payloads_skipped()."""

    @pytest.mark.asyncio
    async def test_yields_regular_lines(self, tmp_path):
        """Test that regular lines are yielded."""
        import aiofiles

        log_file = tmp_path / 'test.log'
        log_file.write_text('Regular line 1\nRegular line 2\n')

        lines = []
        async with aiofiles.open(log_file, 'rb') as f:
            async for line in stream_utils._yield_log_file_with_payloads_skipped(
                    f):
                lines.append(line)

        assert len(lines) == 2
        assert 'Regular line 1' in lines[0]
        assert 'Regular line 2' in lines[1]

    @pytest.mark.asyncio
    async def test_skips_payload_lines(self, tmp_path):
        """Test that payload lines are skipped."""
        import aiofiles

        from sky.utils import message_utils

        log_file = tmp_path / 'test.log'
        payload = message_utils.encode_payload('secret data')
        log_file.write_text(f'Visible\n{payload}Also visible\n')

        lines = []
        async with aiofiles.open(log_file, 'rb') as f:
            async for line in stream_utils._yield_log_file_with_payloads_skipped(
                    f):
                lines.append(line)

        # Verify visible lines are present and payload is filtered
        visible_lines = [l for l in lines if l.strip()]
        assert any('Visible' in l for l in visible_lines)
        assert any('Also visible' in l for l in visible_lines)


class TestBufferFlushBehavior:
    """Tests for buffer flush behavior in log streaming."""

    @pytest.mark.asyncio
    async def test_buffer_flushes_on_size_limit(self, tmp_path):
        """Test that buffer flushes when size limit is reached."""
        import aiofiles

        # Create a large log file
        log_file = tmp_path / 'large.log'
        large_content = 'x' * (stream_utils._BUFFER_SIZE + 100) + '\n'
        log_file.write_text(large_content)

        chunks = []
        async with aiofiles.open(log_file, 'rb') as f:
            async for chunk in stream_utils._tail_log_file(f, follow=False):
                chunks.append(chunk)

        # Should have received content
        combined = ''.join(chunks)
        assert len(combined) > stream_utils._BUFFER_SIZE

    @pytest.mark.asyncio
    async def test_handles_empty_log_file(self, tmp_path):
        """Test handling of empty log file."""
        import aiofiles

        log_file = tmp_path / 'empty.log'
        log_file.write_text('')

        chunks = []
        async with aiofiles.open(log_file, 'rb') as f:
            async for chunk in stream_utils._tail_log_file(f, follow=False):
                chunks.append(chunk)

        # Should handle gracefully
        combined = ''.join(chunks)
        assert combined == ''


class TestRequestStatusTracking:
    """Tests for request status tracking during log streaming."""

    @pytest.fixture
    def mock_request_task(self):
        """Create a mock request task."""
        request = mock.Mock()
        request.request_id = 'test-request-id'
        request.name = 'test.request'
        request.schedule_type = requests_lib.ScheduleType.LONG
        request.status = requests_lib.RequestStatus.RUNNING
        request.status_msg = None
        return request

    @pytest.mark.asyncio
    async def test_waits_for_pending_request(self, mock_request_task, tmp_path):
        """Test that streamer waits for pending request to start."""
        mock_request_task.status = requests_lib.RequestStatus.PENDING

        # Create log file
        log_file = tmp_path / 'test.log'
        log_file.write_text('Log content\n')

        # Mock the status to change from PENDING to RUNNING
        status_call_count = [0]

        async def mock_get_status(request_id, include_msg=False):
            status_call_count[0] += 1
            if status_call_count[0] >= 2:
                return mock.Mock(status=requests_lib.RequestStatus.RUNNING,
                                 status_msg=None)
            return mock.Mock(status=requests_lib.RequestStatus.PENDING,
                             status_msg='Waiting...')

        with mock.patch('sky.server.requests.requests.get_request_async',
                       return_value=mock_request_task), \
             mock.patch('sky.server.requests.requests.get_request_status_async',
                       side_effect=mock_get_status):

            chunks = []
            async for chunk in stream_utils.log_streamer(
                    request_id='test-request-id',
                    log_path=log_file,
                    follow=False,
                    polling_interval=0.01):
                chunks.append(chunk)

        # Should have polled status multiple times
        assert status_call_count[0] >= 1

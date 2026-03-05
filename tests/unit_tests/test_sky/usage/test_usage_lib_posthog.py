"""Tests for PostHog dual-write in usage_lib."""
from unittest import mock

import pytest

from sky.usage import constants
from sky.usage import usage_lib


@pytest.fixture(autouse=True)
def _reset_messages():
    """Reset usage messages before each test."""
    usage_lib.messages.reset(usage_lib.MessageType.USAGE)
    usage_lib.messages.reset(usage_lib.MessageType.HEARTBEAT)
    yield
    usage_lib.messages.reset(usage_lib.MessageType.USAGE)
    usage_lib.messages.reset(usage_lib.MessageType.HEARTBEAT)


@pytest.fixture
def _enable_logging(monkeypatch):
    """Ensure logging is enabled for tests."""
    monkeypatch.delenv('SKYPILOT_DISABLE_USAGE_COLLECTION', raising=False)


@pytest.fixture
def mock_posthog(monkeypatch):
    """Replace posthog_lib LazyImport with a MagicMock.

    Using monkeypatch.setattr avoids mock.patch.object's introspection
    of the LazyImport descriptor, which would trigger an actual import
    of the posthog package.
    """
    mock_ph = mock.MagicMock()
    monkeypatch.setattr(usage_lib, 'posthog_lib', mock_ph)
    return mock_ph


def _setup_usage_message():
    """Set up a UsageMessageToReport with known properties."""
    msg = usage_lib.messages.usage
    msg.user = 'test_user_hash_123'
    msg.cmd = 'sky launch mycluster'
    msg.entrypoint = 'launch'
    msg.cloud = 'aws'
    msg.region = 'us-east-1'
    msg.instance_type = 'p3.2xlarge'
    msg.accelerators = 'V100:1'
    msg.num_nodes = 2
    msg.use_spot = True
    msg.sky_version = '0.10.0'
    msg.start_time = 1000000000000  # 1 second in nanoseconds
    msg.send_time = 1500000000000  # 1.5 seconds in nanoseconds
    return msg


def _setup_unsent_usage_message():
    """Set up a message that _send_local_messages will process.

    message_sent is True when send_time is not None OR start_time is None.
    To make message_sent False (so _send_local_messages processes it),
    we need start_time set and send_time unset.
    """
    msg = _setup_usage_message()
    msg.send_time = None  # message_sent = False since start_time is set
    return msg


class TestSendToPostHog:
    """Tests for _send_to_posthog."""

    def test_calls_capture_with_correct_event(self, mock_posthog,
                                              _enable_logging):
        _setup_usage_message()
        usage_lib._send_to_posthog(usage_lib.MessageType.USAGE)

        mock_posthog.capture.assert_called_once()
        call_kwargs = mock_posthog.capture.call_args
        assert call_kwargs[1]['distinct_id'] == 'test_user_hash_123'
        assert call_kwargs[1]['event'] == 'cli_usage'
        props = call_kwargs[1]['properties']
        assert props['source'] == 'cli'
        assert props['cmd'] == 'sky launch mycluster'
        assert props['cloud'] == 'aws'
        assert props['region'] == 'us-east-1'

    def test_respects_disable_flag(self, mock_posthog, monkeypatch):
        monkeypatch.setenv('SKYPILOT_DISABLE_USAGE_COLLECTION', '1')
        _setup_usage_message()
        usage_lib._send_to_posthog(usage_lib.MessageType.USAGE)

        mock_posthog.capture.assert_not_called()

    def test_truncates_stacktrace(self, mock_posthog, _enable_logging):
        msg = _setup_usage_message()
        msg.stacktrace = 'x' * 10000  # 10KB stacktrace

        usage_lib._send_to_posthog(usage_lib.MessageType.USAGE)

        props = mock_posthog.capture.call_args[1]['properties']
        assert len(props['stacktrace']) <= 4096 + len('...[truncated]')
        assert props['stacktrace'].endswith('...[truncated]')

    def test_includes_sanitized_yamls(self, mock_posthog, _enable_logging):
        msg = _setup_usage_message()
        msg.user_task_yaml = [{'resources': {'cloud': 'aws'}}]
        msg.actual_task = [{'resources': {'cloud': 'aws'}}]
        msg.ray_yamls = [{'cluster_name': 'test'}]

        usage_lib._send_to_posthog(usage_lib.MessageType.USAGE)

        props = mock_posthog.capture.call_args[1]['properties']
        assert props['user_task_yaml'] == [{'resources': {'cloud': 'aws'}}]
        assert props['actual_task'] == [{'resources': {'cloud': 'aws'}}]
        assert props['ray_yamls'] == [{'cluster_name': 'test'}]

    def test_sets_both_api_keys(self, mock_posthog, _enable_logging):
        _setup_usage_message()
        usage_lib._send_to_posthog(usage_lib.MessageType.USAGE)

        assert mock_posthog.api_key == constants.POSTHOG_API_KEY
        assert mock_posthog.project_api_key == constants.POSTHOG_API_KEY
        assert mock_posthog.host == constants.POSTHOG_HOST

    def test_computes_duration_ms(self, mock_posthog, _enable_logging):
        _setup_usage_message()
        # start_time=1000000000000 ns, send_time=1500000000000 ns
        # duration_ms = (1500000000000 - 1000000000000) / 1e6 = 500000.0 ms

        usage_lib._send_to_posthog(usage_lib.MessageType.USAGE)

        props = mock_posthog.capture.call_args[1]['properties']
        assert props['duration_ms'] == 500000.0


class TestSendLocalMessages:
    """Tests for _send_local_messages dual-write behavior."""

    @mock.patch.object(usage_lib, '_send_to_posthog')
    @mock.patch.object(usage_lib, '_send_to_loki')
    def test_calls_both_loki_and_posthog(self, mock_loki, mock_posthog,
                                         _enable_logging):
        _setup_unsent_usage_message()

        usage_lib._send_local_messages()

        mock_loki.assert_called()
        mock_posthog.assert_called()

    @mock.patch.object(usage_lib, '_send_to_posthog')
    @mock.patch.object(usage_lib, '_send_to_loki')
    def test_resets_after_both_sends(self, mock_loki, mock_posthog,
                                     _enable_logging):
        _setup_unsent_usage_message()

        usage_lib._send_local_messages()

        # After _send_local_messages, the message should be reset
        # (message_sent should be True for a fresh message with no start_time)
        assert usage_lib.messages.usage.message_sent

    @mock.patch.object(usage_lib, '_send_to_posthog')
    @mock.patch.object(usage_lib, '_send_to_loki')
    def test_posthog_failure_does_not_block_loki(self, mock_loki, mock_posthog,
                                                 _enable_logging):
        _setup_unsent_usage_message()
        mock_posthog.side_effect = Exception('PostHog error')

        # Should not raise
        usage_lib._send_local_messages()

        mock_loki.assert_called()

    @mock.patch.object(usage_lib, '_send_to_posthog')
    @mock.patch.object(usage_lib, '_send_to_loki')
    def test_loki_failure_does_not_block_posthog(self, mock_loki, mock_posthog,
                                                 _enable_logging):
        _setup_unsent_usage_message()
        mock_loki.side_effect = Exception('Loki error')

        # Should not raise
        usage_lib._send_local_messages()

        mock_posthog.assert_called()


class TestSendHeartbeat:
    """Tests for send_heartbeat dual-write behavior."""

    @mock.patch.object(usage_lib, '_send_to_posthog')
    @mock.patch.object(usage_lib, '_send_to_loki')
    def test_dual_writes(self, mock_loki, mock_posthog, _enable_logging):
        usage_lib.send_heartbeat(interval_seconds=300)

        mock_loki.assert_called_once_with(usage_lib.MessageType.HEARTBEAT)
        mock_posthog.assert_called_once_with(usage_lib.MessageType.HEARTBEAT)

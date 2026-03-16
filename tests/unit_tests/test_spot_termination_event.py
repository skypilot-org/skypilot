"""Tests for spot termination event and preemption hook."""
# pylint: disable=protected-access,unused-argument,import-outside-toplevel
import threading
from unittest import mock
import urllib.error
import urllib.request

import pytest

from sky.skylet import autostop_lib
from sky.skylet import events


class TestPreemptionGuardFlag:
    """Tests for the preemption hook guard flag."""

    def setup_method(self):
        """Reset the guard flag before each test."""
        autostop_lib._preemption_hook_triggered = threading.Event()

    def test_starts_unset(self):
        assert not autostop_lib.is_preemption_hook_triggered()

    def test_set_if_not_set_returns_true_first_time(self):
        assert autostop_lib.set_preemption_hook_if_not_set() is True
        assert autostop_lib.is_preemption_hook_triggered()

    def test_set_if_not_set_returns_false_second_time(self):
        assert autostop_lib.set_preemption_hook_if_not_set() is True
        assert autostop_lib.set_preemption_hook_if_not_set() is False


class TestPreemptionGraceSeconds:
    """Tests for cloud-specific grace period helper."""

    def test_aws_grace_period(self):
        assert autostop_lib.get_preemption_grace_seconds('aws') == 110

    def test_gcp_grace_period(self):
        assert autostop_lib.get_preemption_grace_seconds('gcp') == 25

    def test_azure_grace_period(self):
        assert autostop_lib.get_preemption_grace_seconds('azure') == 25

    def test_kubernetes_uses_default(self):
        # Kubernetes is not supported for preemption hooks (SIGTERM is
        # swallowed by the container entrypoint trap), so it falls through
        # to the conservative default.
        assert autostop_lib.get_preemption_grace_seconds('kubernetes') == 25

    def test_unknown_provider_default(self):
        assert autostop_lib.get_preemption_grace_seconds('unknown') == 25

    def test_timeout_capping(self):
        """Test the min(hook_timeout, grace) logic used by callers."""
        grace = autostop_lib.get_preemption_grace_seconds('aws')  # 110
        # Hook timeout larger than grace -> capped
        assert min(3600, grace) == 110
        # Hook timeout smaller than grace -> not capped
        assert min(60, grace) == 60


class TestCheckAwsSpotTermination:
    """Tests for AWS metadata endpoint checking."""

    @mock.patch('urllib.request.urlopen')
    def test_termination_detected(self, mock_urlopen):
        """200 response means termination is scheduled."""
        mock_token_resp = mock.MagicMock()
        mock_token_resp.read.return_value = b'test-token'

        mock_action_resp = mock.MagicMock()
        mock_action_resp.status = 200

        mock_urlopen.side_effect = [mock_token_resp, mock_action_resp]

        event = events.SpotTerminationEvent()
        assert event._check_aws_spot_termination() is True

    @mock.patch('urllib.request.urlopen')
    def test_no_termination(self, mock_urlopen):
        """404 response means no termination scheduled."""
        mock_token_resp = mock.MagicMock()
        mock_token_resp.read.return_value = b'test-token'

        mock_urlopen.side_effect = [
            mock_token_resp,
            urllib.error.HTTPError(
                url='http://169.254.169.254/latest/meta-data/'
                'spot/instance-action',
                code=404,
                msg='Not Found',
                hdrs=None,
                fp=None),
        ]

        event = events.SpotTerminationEvent()
        assert event._check_aws_spot_termination() is False

    @mock.patch('urllib.request.urlopen')
    def test_network_error(self, mock_urlopen):
        """Network errors should return False (skip)."""
        mock_urlopen.side_effect = urllib.error.URLError('Connection refused')

        event = events.SpotTerminationEvent()
        assert event._check_aws_spot_termination() is False

    @mock.patch('urllib.request.urlopen')
    def test_timeout_error(self, mock_urlopen):
        """Timeout should return False."""
        mock_urlopen.side_effect = OSError('Connection timed out')

        event = events.SpotTerminationEvent()
        assert event._check_aws_spot_termination() is False


class TestSpotTerminationEventStart:
    """Tests for SpotTerminationEvent.start() cloud filtering."""

    @mock.patch('sky.skylet.events.autostop_lib.get_autostop_config')
    @mock.patch('sky.utils.cluster_utils.get_provider_name')
    @mock.patch('sky.utils.yaml_utils.read_yaml')
    def test_starts_thread_on_aws_with_hook(self, mock_read_yaml,
                                            mock_get_provider, mock_get_config):
        mock_get_provider.return_value = 'aws'
        mock_config = mock.MagicMock()
        mock_config.hook = 'echo cleanup'
        mock_config.hook_timeout = 300
        mock_get_config.return_value = mock_config

        with mock.patch('threading.Thread') as mock_thread_cls:
            mock_thread = mock.MagicMock()
            mock_thread_cls.return_value = mock_thread

            event = events.SpotTerminationEvent()
            event.start()

            mock_thread_cls.assert_called_once()
            mock_thread.start.assert_called_once()

    @mock.patch('sky.skylet.events.autostop_lib.get_autostop_config')
    @mock.patch('sky.utils.cluster_utils.get_provider_name')
    @mock.patch('sky.utils.yaml_utils.read_yaml')
    def test_no_thread_on_gcp(self, mock_read_yaml, mock_get_provider,
                              mock_get_config):
        mock_get_provider.return_value = 'gcp'

        with mock.patch('threading.Thread') as mock_thread_cls:
            event = events.SpotTerminationEvent()
            event.start()
            mock_thread_cls.assert_not_called()

    @mock.patch('sky.skylet.events.autostop_lib.get_autostop_config')
    @mock.patch('sky.utils.cluster_utils.get_provider_name')
    @mock.patch('sky.utils.yaml_utils.read_yaml')
    def test_no_thread_on_aws_without_hook(self, mock_read_yaml,
                                           mock_get_provider, mock_get_config):
        mock_get_provider.return_value = 'aws'
        mock_config = mock.MagicMock()
        mock_config.hook = None
        mock_get_config.return_value = mock_config

        with mock.patch('threading.Thread') as mock_thread_cls:
            event = events.SpotTerminationEvent()
            event.start()
            mock_thread_cls.assert_not_called()


class TestIsKubernetes:
    """Tests for the _is_kubernetes check in skylet.py."""

    @mock.patch('sky.utils.cluster_utils.get_provider_name',
                return_value='kubernetes')
    @mock.patch('sky.utils.yaml_utils.read_yaml', return_value={})
    def test_returns_true_on_k8s(self, mock_read_yaml, mock_get_provider):
        from sky.skylet import skylet
        assert skylet._is_kubernetes() is True

    @mock.patch('sky.utils.cluster_utils.get_provider_name', return_value='aws')
    @mock.patch('sky.utils.yaml_utils.read_yaml', return_value={})
    def test_returns_false_on_aws(self, mock_read_yaml, mock_get_provider):
        from sky.skylet import skylet
        assert skylet._is_kubernetes() is False

    @mock.patch('sky.utils.yaml_utils.read_yaml', side_effect=FileNotFoundError)
    def test_returns_false_on_error(self, mock_read_yaml):
        from sky.skylet import skylet
        assert skylet._is_kubernetes() is False


class TestSigtermHandler:
    """Tests for the SIGTERM handler in skylet.py."""

    def setup_method(self):
        """Reset the guard flag before each test."""
        autostop_lib._preemption_hook_triggered = threading.Event()

    @mock.patch('sky.skylet.autostop_lib.execute_autostop_hook')
    @mock.patch('sky.utils.cluster_utils.get_provider_name', return_value='aws')
    @mock.patch('sky.utils.yaml_utils.read_yaml', return_value={})
    @mock.patch('sky.skylet.autostop_lib.get_autostop_config')
    def test_sigterm_runs_hook(self, mock_config, mock_read_yaml,
                               mock_get_provider, mock_exec_hook):
        from sky.skylet import skylet

        config = mock.MagicMock()
        config.hook = 'echo cleanup'
        config.hook_timeout = 3600
        mock_config.return_value = config

        with pytest.raises(SystemExit):
            skylet._handle_sigterm(None, None)

        mock_exec_hook.assert_called_once()
        # Verify timeout was capped to AWS grace (110s)
        call_args = mock_exec_hook.call_args
        assert call_args[0][0] == 'echo cleanup'
        assert call_args[0][1] == 110  # capped_timeout

    @mock.patch('sky.skylet.autostop_lib.execute_autostop_hook')
    @mock.patch('sky.skylet.autostop_lib.get_autostop_config')
    def test_sigterm_skips_when_no_hook(self, mock_config, mock_exec_hook):
        from sky.skylet import skylet

        config = mock.MagicMock()
        config.hook = None
        mock_config.return_value = config

        with pytest.raises(SystemExit):
            skylet._handle_sigterm(None, None)

        mock_exec_hook.assert_not_called()

    @mock.patch('sky.skylet.autostop_lib.execute_autostop_hook')
    @mock.patch('sky.skylet.autostop_lib.get_autostop_config')
    def test_sigterm_guard_prevents_double(self, mock_config, mock_exec_hook):
        from sky.skylet import skylet

        # Pre-set the guard flag
        autostop_lib.set_preemption_hook_if_not_set()

        with pytest.raises(SystemExit):
            skylet._handle_sigterm(None, None)

        # Hook should NOT be called since guard was already set
        mock_exec_hook.assert_not_called()
        # get_autostop_config should not even be called
        mock_config.assert_not_called()

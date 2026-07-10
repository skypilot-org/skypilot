"""Unit tests for the logging-agent provider hook in sky.logs."""

import unittest
from unittest import mock

import sky.logs as logs
from sky.logs.agent import LoggingAgent


class _FakeAgent(LoggingAgent):
    """Minimal LoggingAgent stand-in for the provider tests."""

    def get_setup_command(self, cluster_name):
        return 'true'

    def get_credential_file_mounts(self):
        return {}


class TestLoggingAgentProvider(unittest.TestCase):

    def tearDown(self):
        # Always clear the process-global provider between tests.
        logs.register_logging_agent_provider(None)

    def test_no_provider_no_config_returns_none(self):
        with mock.patch('sky.skypilot_config.get_nested', return_value=None):
            self.assertIsNone(logs.get_logging_agent())

    def test_provider_takes_precedence_over_config(self):
        agent = _FakeAgent()
        logs.register_logging_agent_provider(lambda: agent)
        # Even with no logs.store configured, the provider's agent is returned.
        with mock.patch('sky.skypilot_config.get_nested',
                        return_value=None) as m:
            self.assertIs(logs.get_logging_agent(), agent)
            # Config selection is skipped entirely when the provider returns one.
            m.assert_not_called()

    def test_provider_returning_none_falls_back_to_config(self):
        logs.register_logging_agent_provider(lambda: None)

        def fake_get_nested(keys, default=None):
            return 'gcp' if keys == ('logs', 'store') else {}

        with mock.patch('sky.skypilot_config.get_nested',
                        side_effect=fake_get_nested):
            agent = logs.get_logging_agent()
        self.assertIsInstance(agent, logs.GCPLoggingAgent)

    def test_clearing_provider_restores_config_path(self):
        logs.register_logging_agent_provider(lambda: _FakeAgent())
        logs.register_logging_agent_provider(None)
        with mock.patch('sky.skypilot_config.get_nested', return_value=None):
            self.assertIsNone(logs.get_logging_agent())

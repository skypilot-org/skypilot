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

    def test_placement_aware_provider_receives_cloud_and_region(self):
        agent = _FakeAgent()
        seen = []

        def provider(*, cloud=None, region=None):
            seen.append((cloud, region))
            return agent

        logs.register_logging_agent_provider(provider)
        self.assertIs(
            logs.get_logging_agent(cloud='kubernetes', region='ctx-a'), agent)
        self.assertIs(logs.get_logging_agent(), agent)
        self.assertEqual(seen, [('kubernetes', 'ctx-a'), (None, None)])

    def test_provider_without_parameters_is_called_without_placement(self):
        agent = _FakeAgent()
        calls = []

        def provider():
            calls.append(True)
            return agent

        logs.register_logging_agent_provider(provider)
        self.assertIs(
            logs.get_logging_agent(cloud='kubernetes', region='ctx-a'), agent)
        self.assertEqual(calls, [True])

    def test_provider_accepting_kwargs_receives_placement(self):
        seen = {}

        def provider(**kwargs):
            seen.update(kwargs)
            return _FakeAgent()

        logs.register_logging_agent_provider(provider)
        logs.get_logging_agent(cloud='aws', region='us-east-1')
        self.assertEqual(seen, {'cloud': 'aws', 'region': 'us-east-1'})

    def test_provider_is_published_with_its_signature(self):
        logs.register_logging_agent_provider(lambda: _FakeAgent())
        no_arg = logs._logging_agent_provider  # pylint: disable=protected-access

        def provider(*, cloud=None, region=None):
            del cloud, region
            return _FakeAgent()

        logs.register_logging_agent_provider(provider)
        placed = logs._logging_agent_provider  # pylint: disable=protected-access
        self.assertFalse(no_arg[1])
        self.assertEqual(placed, (provider, True))

    def test_is_configured_routes_by_placement(self):

        def provider(*, cloud=None, region=None):
            del cloud
            return _FakeAgent() if region == 'ctx-a' else None

        logs.register_logging_agent_provider(provider)
        with mock.patch('sky.skypilot_config.get_nested', return_value=None):
            self.assertTrue(
                logs.is_logging_agent_configured(cloud='kubernetes',
                                                 region='ctx-a'))
            self.assertFalse(
                logs.is_logging_agent_configured(cloud='kubernetes',
                                                 region='ctx-b'))

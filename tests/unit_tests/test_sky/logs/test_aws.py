"""Unit tests for sky.logs.aws module."""

import unittest
from unittest import mock

import yaml

from sky.logs.aws import CloudwatchLoggingAgent
from sky.utils import resources_utils


class TestCloudwatchLoggingAgent(unittest.TestCase):
    """Tests for CloudwatchLoggingAgent."""

    def setUp(self):
        """Set up test fixtures."""
        self.cluster_name = resources_utils.ClusterName(
            'test-cluster', 'test-cluster-unique-id')

    def test_init(self):
        """Test initialization with different configurations."""
        # Test with empty config
        agent = CloudwatchLoggingAgent({})
        self.assertIsNone(agent.config.region)
        self.assertIsNone(agent.config.credentials_file)
        self.assertEqual(agent.config.log_group_name, 'skypilot-logs')
        self.assertEqual(agent.config.log_stream_prefix, 'skypilot-')
        self.assertTrue(agent.config.auto_create_group)
        self.assertIsNone(agent.config.additional_tags)

        # Test with full config
        config = {
            'region': 'us-west-2',
            'credentials_file': '/path/to/credentials',
            'log_group_name': 'my-logs',
            'log_stream_prefix': 'my-prefix-',
            'auto_create_group': False,
            'additional_tags': {
                'env': 'test'
            },
        }
        agent = CloudwatchLoggingAgent(config)
        self.assertEqual(agent.config.region, 'us-west-2')
        self.assertEqual(agent.config.credentials_file, '/path/to/credentials')
        self.assertEqual(agent.config.log_group_name, 'my-logs')
        self.assertEqual(agent.config.log_stream_prefix, 'my-prefix-')
        self.assertFalse(agent.config.auto_create_group)
        self.assertDictEqual(agent.config.additional_tags, {'env': 'test'})

    def test_get_credential_file_mounts(self):
        """Test get_credential_file_mounts method."""
        # Test with no credentials file
        agent = CloudwatchLoggingAgent({})
        self.assertEqual(agent.get_credential_file_mounts(), {})

        # Test with credentials file
        agent = CloudwatchLoggingAgent(
            {'credentials_file': '/path/to/credentials'})
        self.assertEqual(agent.get_credential_file_mounts(),
                         {'/path/to/credentials': '/path/to/credentials'})

    def test_fluentbit_output_config(self):
        """Test fluentbit_output_config method."""
        # Test with default config
        agent = CloudwatchLoggingAgent({})
        output_config = agent.fluentbit_output_config(self.cluster_name)
        self.assertEqual(output_config['name'], 'cloudwatch_logs')
        self.assertEqual(output_config['match'], '*')
        self.assertEqual(output_config['log_group_name'], 'skypilot-logs')
        self.assertEqual(output_config['log_stream_prefix'],
                         f'skypilot-{self.cluster_name.name_on_cloud}-')
        self.assertEqual(output_config['auto_create_group'], 'true')

        # Test with custom config
        config = {
            'region': 'us-west-2',
            'log_group_name': 'my-logs',
            'log_stream_prefix': 'my-prefix-',
            'auto_create_group': False,
        }
        agent = CloudwatchLoggingAgent(config)
        output_config = agent.fluentbit_output_config(self.cluster_name)
        self.assertEqual(output_config['region'], 'us-west-2')
        self.assertEqual(output_config['log_group_name'], 'my-logs')
        self.assertEqual(output_config['log_stream_prefix'],
                         f'my-prefix-{self.cluster_name.name_on_cloud}-')
        self.assertEqual(output_config['auto_create_group'], 'false')

    @mock.patch('sky.logs.agent.FluentbitAgent.get_setup_command')
    def test_get_setup_command(self, mock_super_get_setup_command):
        """Test get_setup_command method."""
        mock_super_get_setup_command.return_value = 'super_command'

        # Test with credentials file
        agent = CloudwatchLoggingAgent(
            {'credentials_file': '/path/to/credentials'})
        setup_cmd = agent.get_setup_command(self.cluster_name)
        self.assertIn('export AWS_SHARED_CREDENTIALS_FILE=/path/to/credentials',
                      setup_cmd)
        self.assertIn('super_command', setup_cmd)

        # Test with region
        agent = CloudwatchLoggingAgent({'region': 'us-west-2'})
        setup_cmd = agent.get_setup_command(self.cluster_name)
        self.assertIn('export AWS_REGION=us-west-2', setup_cmd)
        self.assertIn('super_command', setup_cmd)

        # Test without region
        agent = CloudwatchLoggingAgent({})
        setup_cmd = agent.get_setup_command(self.cluster_name)
        self.assertIn('if [ -z "$AWS_REGION" ] && [ -z "$AWS_DEFAULT_REGION" ]',
                      setup_cmd)
        self.assertIn('super_command', setup_cmd)

    def test_fluentbit_config(self):
        """Test fluentbit_config method."""
        agent = CloudwatchLoggingAgent({})
        config_str = agent.fluentbit_config(self.cluster_name)
        self.assertIn('pipeline:', config_str)
        self.assertIn('inputs:', config_str)
        self.assertIn('outputs:', config_str)
        self.assertIn('name: cloudwatch', config_str)

        # parse yaml and check if the config is valid
        config_dict = yaml.safe_load(config_str)
        self.assertEqual(
            config_dict['pipeline']['outputs'][0]['log_group_name'],
            'skypilot-logs')
        self.assertEqual(config_dict['pipeline']['inputs'][0]['name'], 'tail')
        self.assertDictEqual(
            config_dict['pipeline']['inputs'][0]['processors']['logs'][0], {
                'name': 'content_modifier',
                'action': 'upsert',
                'key': 'skypilot.cluster_name',
                'value': self.cluster_name.display_name,
            })

"""Unit tests for sky.logs.gcp module."""

import os
import tempfile
import unittest
from unittest import mock

from sky.clouds import gcp
from sky.logs.gcp import _REMOTE_CREDENTIAL_PATH
from sky.logs.gcp import GCPLoggingAgent
from sky.utils import resources_utils


class TestGCPLoggingAgent(unittest.TestCase):
    """Tests for GCPLoggingAgent credential handling."""

    def setUp(self):
        self.cluster_name = resources_utils.ClusterName(
            'test-cluster', 'test-cluster-unique-id')

    def test_get_credential_file_mounts_none(self):
        """No credentials_file -> no logging-agent credential upload."""
        agent = GCPLoggingAgent({})
        self.assertEqual(agent.get_credential_file_mounts(), {})

    def test_get_credential_file_mounts_resolves_symlink(self):
        """A key behind a symlink (e.g. a K8s Secret volume) is uploaded by
        its resolved real path, to the fixed home-relative destination."""
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, 'real-key.json')
            with open(real, 'w', encoding='utf-8') as f:
                f.write('{}')
            link = os.path.join(d, 'link-key.json')
            os.symlink(real, link)

            agent = GCPLoggingAgent({'credentials_file': link})
            self.assertEqual(agent.get_credential_file_mounts(),
                             {_REMOTE_CREDENTIAL_PATH: os.path.realpath(real)})

    def test_get_credential_file_mounts_expands_user(self):
        """'~' in the configured path is expanded before upload."""
        with tempfile.TemporaryDirectory() as home:
            real = os.path.join(home, 'key.json')
            with open(real, 'w', encoding='utf-8') as f:
                f.write('{}')
            with mock.patch.dict(os.environ, {'HOME': home}):
                agent = GCPLoggingAgent({'credentials_file': '~/key.json'})
                mounts = agent.get_credential_file_mounts()
            self.assertEqual(mounts,
                             {_REMOTE_CREDENTIAL_PATH: os.path.realpath(real)})

    def test_setup_command_uses_remote_path_when_key_set(self):
        """When a key is configured, GOOGLE_APPLICATION_CREDENTIALS points at
        the delivered remote path, not the (server-side) source path."""
        agent = GCPLoggingAgent({'credentials_file': '/server/only/key.json'})
        cmd = agent.get_setup_command(self.cluster_name)
        self.assertIn(
            f'export GOOGLE_APPLICATION_CREDENTIALS={_REMOTE_CREDENTIAL_PATH}',
            cmd)
        self.assertNotIn('/server/only/key.json', cmd)

    def test_setup_command_defaults_to_adc_when_no_key(self):
        """Without a key, fall back to the default application-default path."""
        agent = GCPLoggingAgent({})
        cmd = agent.get_setup_command(self.cluster_name)
        self.assertIn(
            'export GOOGLE_APPLICATION_CREDENTIALS='
            f'{gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH}', cmd)


if __name__ == '__main__':
    unittest.main()

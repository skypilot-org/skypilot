"""Tests for Slurm cloud implementation."""

import base64
import os
import tempfile
from unittest.mock import MagicMock
from unittest.mock import patch
import unittest.mock as mock

import pytest

from sky.adaptors import slurm
from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm import utils as slurm_utils
from sky.server.requests import payloads
from sky.utils import context


class TestCheckInstanceFits:
    """Test slurm_utils.check_instance_fits()."""

    node_4cpu_16gb = slurm.NodeInfo(node='node1',
                                    state='idle',
                                    gres='(null)',
                                    cpus=4,
                                    memory_gb=16,
                                    partition='dev*')

    node_2cpu_8gb = slurm.NodeInfo(node='node1',
                                   state='idle',
                                   gres='(null)',
                                   cpus=2,
                                   memory_gb=8,
                                   partition='dev*')

    node_gpu_a10g = slurm.NodeInfo(node='node1',
                                   state='idle',
                                   gres='gpu:a10g:8',
                                   cpus=192,
                                   memory_gb=786,
                                   partition='gpus')

    node_2cpu_8gb_cpus = slurm.NodeInfo(node='node2',
                                        state='idle',
                                        gres='(null)',
                                        cpus=2,
                                        memory_gb=8,
                                        partition='dev*')

    @pytest.mark.parametrize(
        'nodes,instance_type,partition,expected_fits,reason_contains',
        [
            # No accelerators - fits
            ([node_4cpu_16gb], '2CPU--8GB', None, True, None),
            # No accelerators - insufficient resources
            ([node_2cpu_8gb], '4CPU--16GB', None, False, 'Max found: 2 CPUs'),
            # GPU - fits
            ([node_gpu_a10g], '64CPU--256GB--a10g:4', None, True, None),
            # GPU - type not available
            ([node_gpu_a10g
             ], '64CPU--256GB--h100:4', None, False, 'No GPU nodes found'),
            # Partition filtering with default partition (*) handling
            ([node_2cpu_8gb_cpus], '1CPU--4GB', 'dev', True, None),
            # Resource exists but in different partition
            ([node_2cpu_8gb_cpus
             ], '2CPU--8GB', 'different_partition', False, 'No nodes found'),
        ])
    @patch('sky.provision.slurm.utils.get_cluster_default_partition')
    @patch('sky.provision.slurm.utils.slurm.SlurmClient')
    @patch('sky.provision.slurm.utils.SSHConfig.from_path')
    def test_check_instance_fits(self, mock_ssh_config, mock_slurm_client,
                                 mock_default_partition, nodes, instance_type,
                                 partition, expected_fits, reason_contains):
        """Test various scenarios for instance fitting."""
        mock_default_partition.return_value = 'dev'
        mock_ssh_config_obj = mock.MagicMock()
        mock_ssh_config_obj.lookup.return_value = {
            'hostname': '10.0.0.1',
            'port': '22',
            'user': 'slurm',
            'identityfile': ['/home/user/.ssh/id_rsa'],
        }
        mock_ssh_config.return_value = mock_ssh_config_obj

        mock_slurm_client_instance = mock.MagicMock()
        mock_slurm_client_instance.info_nodes.return_value = nodes
        mock_slurm_client.return_value = mock_slurm_client_instance

        kwargs = {'cluster': 'hyperpod', 'instance_type': instance_type}
        if partition:
            kwargs['partition'] = partition

        fits, reason = slurm_utils.check_instance_fits(**kwargs)

        assert fits is expected_fits
        if reason_contains:
            assert reason_contains in reason
        else:
            assert reason is None


class TestTerminateInstances:
    """Test slurm_instance.terminate_instances()."""

    @pytest.mark.parametrize(
        'job_state,should_cancel,should_signal',
        [
            # Terminal states - no action needed
            ('COMPLETED', False, False),
            ('CANCELLED', False, False),
            ('FAILED', False, False),
            ('TIMEOUT', False, False),
            ('NODE_FAIL', False, False),
            ('PREEMPTED', False, False),
            ('SPECIAL_EXIT', False, False),
            # COMPLETING - already terminating
            ('COMPLETING', False, False),
            # PENDING and CONFIGURING - cancel without signal
            ('PENDING', True, False),
            ('CONFIGURING', True, False),
            # Other states - cancel with TERM signal
            ('RUNNING', True, True),
            ('SUSPENDED', True, True),
            ('STAGING_OUT', True, True),
        ])
    @patch('sky.provision.slurm.instance.slurm_utils.is_inside_slurm_job')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    def test_terminate_instances_handles_job_states(self,
                                                    mock_slurm_client_class,
                                                    mock_is_inside_job,
                                                    job_state, should_cancel,
                                                    should_signal):
        """Test terminate_instances handles different job states correctly."""
        mock_is_inside_job.return_value = False

        mock_client = mock.MagicMock()
        mock_slurm_client_class.return_value = mock_client

        cluster_name = 'test-cluster'
        provider_config = {
            'ssh': {
                'hostname': 'localhost',
                'port': '22',
                'user': 'testuser',
                'private_key': '/path/to/key',
            }
        }

        # Mock the job state query
        mock_client.get_jobs_state_by_name.return_value = [job_state]

        slurm_instance.terminate_instances(
            cluster_name_on_cloud=cluster_name,
            provider_config=provider_config,
        )

        if should_cancel:
            if should_signal:
                mock_client.cancel_jobs_by_name.assert_called_once_with(
                    cluster_name,
                    signal='TERM',
                    full=True,
                )
            else:
                mock_client.cancel_jobs_by_name.assert_called_once_with(
                    cluster_name,
                    signal=None,
                )
        else:
            mock_client.cancel_jobs_by_name.assert_not_called()

    @patch('sky.provision.slurm.instance.slurm_utils.is_inside_slurm_job')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    def test_terminate_instances_no_jobs_found(self, mock_slurm_client_class,
                                               mock_is_inside_job):
        """Test terminate_instances when no jobs are found."""
        mock_is_inside_job.return_value = False

        mock_client = mock.MagicMock()
        mock_slurm_client_class.return_value = mock_client

        cluster_name = 'test-cluster'
        provider_config = {
            'ssh': {
                'hostname': 'localhost',
                'port': '22',
                'user': 'testuser',
                'private_key': '/path/to/key',
            }
        }

        # No jobs found
        mock_client.get_jobs_state_by_name.return_value = []

        slurm_instance.terminate_instances(
            cluster_name_on_cloud=cluster_name,
            provider_config=provider_config,
        )

        # Should return early without canceling
        mock_client.cancel_jobs_by_name.assert_not_called()


class TestGetSlurmSshConfigDict:
    """Test slurm_utils.get_slurm_ssh_config_dict()."""

    @patch('sky.provision.slurm.utils.get_slurm_ssh_config')
    def test_server_side_config_only(self, mock_get_ssh_config):
        """Test reading SSH config on server without credential overlay."""
        mock_ssh_config = MagicMock()
        mock_ssh_config.lookup.return_value = {
            'hostname': '10.0.0.1',
            'port': '22',
            'user': 'slurm-user',
            'identityfile': ['/home/user/.ssh/id_rsa'],
            'proxycommand': 'ssh -W %h:%p bastion',
        }
        mock_get_ssh_config.return_value = mock_ssh_config

        result = slurm_utils.get_slurm_ssh_config_dict('my-cluster')

        assert result['hostname'] == '10.0.0.1'
        assert result['user'] == 'slurm-user'
        assert result['identityfile'] == ['/home/user/.ssh/id_rsa']
        assert result['proxycommand'] == 'ssh -W %h:%p bastion'
        mock_ssh_config.lookup.assert_called_once_with('my-cluster')

    @pytest.mark.parametrize('use_context', [False, True])
    @patch('sky.provision.slurm.utils.get_slurm_ssh_config')
    def test_client_credential_overlay(self, mock_get_ssh_config, use_context):
        """Test client credential overlay."""
        mock_ssh_config = MagicMock()
        mock_ssh_config.lookup.return_value = {
            'hostname': '10.0.0.1',
            'port': '22',
            'user': 'root',
            'identityfile': ['/root/.ssh/id_rsa'],
        }
        mock_get_ssh_config.return_value = mock_ssh_config

        test_key_content = b'-----BEGIN RSA PRIVATE KEY-----\ntest-key-data\n-----END RSA PRIVATE KEY-----\n'
        credentials = {
            'my-cluster': payloads.SlurmSSHCredential(
                ssh_user='bob',
                ssh_private_key_base64=base64.b64encode(
                    test_key_content).decode('utf-8'),
            )
        }

        def run_test():
            slurm_utils.set_client_ssh_credentials(credentials)
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    with patch.object(slurm_utils, 'SLURM_SSH_KEY_CACHE_DIR',
                                      tmpdir):
                        result = slurm_utils.get_slurm_ssh_config_dict(
                            'my-cluster')

                        assert result['user'] == 'bob'
                        assert result['hostname'] == '10.0.0.1'
                        assert len(result['identityfile']) == 1

                        key_path = result['identityfile'][0]
                        assert os.path.exists(key_path)
                        assert os.path.dirname(os.path.dirname(key_path)) == tmpdir

                        with open(key_path, 'rb') as f:
                            assert f.read() == test_key_content
                        # Extract just the permission bits
                        assert oct(os.stat(key_path).st_mode)[-3:] == '600'
            finally:
                slurm_utils.set_client_ssh_credentials(None)

        if use_context:
            # Test coroutine case with context
            with context.initialize():
                run_test()
        else:
            # Test multiprocess worker case
            run_test()

    @patch('sky.provision.slurm.utils.get_slurm_ssh_config')
    def test_key_caching(self, mock_get_ssh_config):
        """Test key caching: reuses same key, creates new cache for different key."""
        mock_ssh_config = MagicMock()
        mock_ssh_config.lookup.return_value = {
            'hostname': '10.0.0.1',
            'port': '22',
            'user': 'root',
            'identityfile': ['/root/.ssh/id_rsa'],
        }
        mock_get_ssh_config.return_value = mock_ssh_config

        key1_content = b'-----BEGIN RSA PRIVATE KEY-----\nkey1\n-----END RSA PRIVATE KEY-----\n'
        key2_content = b'-----BEGIN RSA PRIVATE KEY-----\nkey2\n-----END RSA PRIVATE KEY-----\n'

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch.object(slurm_utils, 'SLURM_SSH_KEY_CACHE_DIR',
                                  tmpdir):
                    # Step 1: Use key1 - creates cache
                    slurm_utils.set_client_ssh_credentials({
                        'my-cluster': payloads.SlurmSSHCredential(
                            ssh_user='user1',
                            ssh_private_key_base64=base64.b64encode(
                                key1_content).decode('utf-8'),
                        )
                    })
                    result1 = slurm_utils.get_slurm_ssh_config_dict(
                        'my-cluster')
                    key_path1 = result1['identityfile'][0]
                    mtime1 = os.path.getmtime(key_path1)

                    # Step 2: Use key1 again - reuses cache
                    result2 = slurm_utils.get_slurm_ssh_config_dict(
                        'my-cluster')
                    key_path2 = result2['identityfile'][0]
                    mtime2 = os.path.getmtime(key_path2)

                    assert key_path1 == key_path2
                    assert mtime1 == mtime2  # File not rewritten

                    # Step 3: Use key2 - creates new cache
                    slurm_utils.set_client_ssh_credentials({
                        'my-cluster': payloads.SlurmSSHCredential(
                            ssh_user='user2',
                            ssh_private_key_base64=base64.b64encode(
                                key2_content).decode('utf-8'),
                        )
                    })
                    result3 = slurm_utils.get_slurm_ssh_config_dict(
                        'my-cluster')
                    key_path3 = result3['identityfile'][0]

                    # Different keys create different cache files
                    assert key_path3 != key_path1

                    # Both cache files exist
                    assert os.path.exists(key_path1)
                    assert os.path.exists(key_path3)

                    # Verify contents
                    with open(key_path1, 'rb') as f:
                        assert f.read() == key1_content
                    with open(key_path3, 'rb') as f:
                        assert f.read() == key2_content
        finally:
            slurm_utils.set_client_ssh_credentials(None)

    @patch('sky.provision.slurm.utils.get_slurm_ssh_config')
    def test_missing_cluster_in_credentials(self, mock_get_ssh_config):
        """Test that config is not modified when cluster not in credentials."""
        mock_ssh_config = MagicMock()
        mock_ssh_config.lookup.return_value = {
            'hostname': '10.0.0.1',
            'port': '22',
            'user': 'root',
            'identityfile': ['/root/.ssh/id_rsa'],
        }
        mock_get_ssh_config.return_value = mock_ssh_config

        slurm_utils.set_client_ssh_credentials({
            'other-cluster': payloads.SlurmSSHCredential(
                ssh_user='alice',
                ssh_private_key_base64='dGVzdC1rZXk=',
            )
        })

        try:
            result = slurm_utils.get_slurm_ssh_config_dict('my-cluster')
            assert result['user'] == 'root'
            assert result['identityfile'] == ['/root/.ssh/id_rsa']
        finally:
            slurm_utils.set_client_ssh_credentials(None)

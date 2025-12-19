"""Tests for Slurm cloud implementation."""

from unittest.mock import patch
import unittest.mock as mock

import pytest

from sky.adaptors import slurm
from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm import utils as slurm_utils


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
    @patch('sky.provision.slurm.instance.slurm_utils.is_inside_slurm_cluster')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    def test_terminate_instances_handles_job_states(
            self, mock_slurm_client_class, mock_is_inside_slurm_cluster,
            job_state, should_cancel, should_signal):
        """Test terminate_instances handles different job states correctly."""
        mock_is_inside_slurm_cluster.return_value = False

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

    @patch('sky.provision.slurm.instance.slurm_utils.is_inside_slurm_cluster')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    def test_terminate_instances_no_jobs_found(self, mock_slurm_client_class,
                                               mock_is_inside_slurm_cluster):
        """Test terminate_instances when no jobs are found."""
        mock_is_inside_slurm_cluster.return_value = False

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

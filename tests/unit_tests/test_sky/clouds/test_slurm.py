"""Tests for Slurm cloud implementation."""

import math
import os
from pathlib import Path
from unittest.mock import patch
import unittest.mock as mock

import pytest

from sky import resources as resources_lib
from sky.adaptors import slurm
from sky.clouds import slurm as slurm_cloud
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
    @patch('sky.provision.slurm.utils.kv_cache.get_cache_entry',
           return_value=None)
    @patch('sky.provision.slurm.utils.get_cluster_default_partition')
    @patch('sky.provision.slurm.utils.slurm.SlurmClient')
    @patch('sky.provision.slurm.utils.SSHConfig.from_path')
    def test_check_instance_fits(self, mock_ssh_config, mock_slurm_client,
                                 mock_default_partition, mock_kv_cache, nodes,
                                 instance_type, partition, expected_fits,
                                 reason_contains):
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


class TestSlurmGPUDefaults:
    """Test Slurm GPU default CPU and memory allocation.

    These tests verify that when GPU instances are requested without explicit
    CPU/memory specifications, Slurm allocates reasonable defaults matching
    Kubernetes behavior (4 CPUs and 16GB memory per GPU).
    """

    @pytest.mark.parametrize(
        'gpu_count,expected_cpus,expected_memory',
        [
            (1, 4, 16.0),  # 1 GPU: 4 CPUs, 16GB
            (2, 8, 32.0),  # 2 GPUs: 8 CPUs, 32GB
            (4, 16, 64.0),  # 4 GPUs: 16 CPUs, 64GB
            (8, 32, 128.0),  # 8 GPUs: 32 CPUs, 128GB
        ])
    @patch('sky.clouds.slurm.Slurm.regions_with_offering')
    def test_gpu_defaults_without_explicit_cpu_memory(self, mock_regions,
                                                      gpu_count, expected_cpus,
                                                      expected_memory):
        """Test GPU instances get correct default CPU and memory allocation."""
        mock_region = mock.MagicMock()
        mock_region.name = 'test-cluster'
        mock_regions.return_value = [mock_region]

        # Create resources with GPU but no explicit CPU/memory
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            accelerators={f'H200': gpu_count},
            # No cpus or memory specified - should use defaults
        )

        cloud = slurm_cloud.Slurm()
        feasible = cloud._get_feasible_launchable_resources(resources)

        assert len(feasible.resources_list) == 1
        resource = feasible.resources_list[0]

        instance_type = slurm_utils.SlurmInstanceType.from_instance_type(
            resource.instance_type)
        assert instance_type.cpus == expected_cpus
        assert instance_type.memory == expected_memory
        assert instance_type.accelerator_count == gpu_count
        assert instance_type.accelerator_type == 'H200'

    @pytest.mark.parametrize(
        'accelerators,cpus,memory,expected_cpus,expected_memory',
        [
            # Various GPU types with defaults
            ({
                'H200': 2
            }, None, None, 8, 32.0),
            ({
                'A100': 2
            }, None, None, 8, 32.0),
            ({
                'H100': 2
            }, None, None, 8, 32.0),
            ({
                'A10G': 2
            }, None, None, 8, 32.0),
            # Explicit CPU override (memory scales)
            ({
                'H200': 2
            }, '16', None, 16, 64.0),
            # Explicit memory override (CPU uses default)
            ({
                'H200': 1
            }, None, '32', 4, 32.0),
            # Both CPU and memory override
            ({
                'H200': 2
            }, '32', '64', 32, 64.0),
            # Memory with '+' suffix
            ({
                'H200': 1
            }, None, '32+', 4, 32.0),
            # CPU-only instance (basic defaults)
            (None, None, None, 2, 2.0),
        ])
    @patch('sky.clouds.slurm.Slurm.regions_with_offering')
    def test_resource_allocation_scenarios(self, mock_regions, accelerators,
                                           cpus, memory, expected_cpus,
                                           expected_memory):
        """Test various resource allocation scenarios including GPU types and overrides."""
        mock_region = mock.MagicMock()
        mock_region.name = 'test-cluster'
        mock_regions.return_value = [mock_region]

        kwargs = {'cloud': slurm_cloud.Slurm()}
        if accelerators:
            kwargs['accelerators'] = accelerators
        if cpus:
            kwargs['cpus'] = cpus
        if memory:
            kwargs['memory'] = memory

        resources = resources_lib.Resources(**kwargs)
        cloud = slurm_cloud.Slurm()
        feasible = cloud._get_feasible_launchable_resources(resources)

        resource = feasible.resources_list[0]
        instance_type = slurm_utils.SlurmInstanceType.from_instance_type(
            resource.instance_type)

        assert instance_type.cpus == expected_cpus
        assert instance_type.memory == expected_memory


class TestGRESGPUParsing:
    """Test slurm_utils.get_gpu_type_and_count."""

    @pytest.mark.parametrize(
        'gres_str,expected_type,expected_count',
        [
            # Standard formats
            ('gpu:H100:8', 'H100', 8),
            ('gpu:a10g:4', 'a10g', 4),
            ('gpu:nvidia_h100_80gb_hbm3:8', 'nvidia_h100_80gb_hbm3', 8),
            ('gpu:A100_sxm4_40gb:4', 'A100_sxm4_40gb', 4),
            # No type
            ('gpu:8', None, 8),
            ('gpu:1', None, 1),
            # With extra Slurm info
            ('gpu:H100:8(S:0-1)', 'H100', 8),
            ('gpu:8(S:0)', None, 8),
            # Embedded in larger GRES string
            ('nic:1,gpu:H100:2,license:1', 'H100', 2),
            # Different casings for 'gpu'
            ('GPU:V100:1', 'V100', 1),
            ('Gpu:8', None, 8),
            # No match
            ('(null)', None, 0),
            ('N/A', None, 0),
            ('nic:1', None, 0),
            ('gpu', None, 0),
            ('gpu:', None, 0),
            ('not_a_gpu:8', None, 0),
            ('mygpu:8', None, 0),
        ])
    def test_get_gpu_type_and_count(self, gres_str, expected_type,
                                    expected_count):
        """Test that the helper correctly parses various GRES strings."""
        gpu_type, gpu_count = slurm_utils.get_gpu_type_and_count(gres_str)
        assert gpu_type == expected_type
        assert gpu_count == expected_count


SBATCH_TESTDATA_DIR = Path(__file__).parent / 'testdata' / 'slurm_sbatch'


def assert_sbatch_matches_snapshot(test_name: str,
                                   generated_script: str) -> None:
    """Compare generated sbatch script against snapshot file.

    Args:
        test_name: Name of the test (used to find snapshot file)
        generated_script: The script generated by _create_virtual_instance

    If UPDATE_SNAPSHOT=1 env var is set, updates the snapshot file instead
    of comparing.
    """
    snapshot_path = SBATCH_TESTDATA_DIR / f'{test_name}.sh'

    if os.environ.get('UPDATE_SNAPSHOT') == '1':
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(generated_script)
        print(f'Updated snapshot: {snapshot_path}')
        return

    if not snapshot_path.exists():
        pytest.fail(f'Snapshot file not found: {snapshot_path}\n'
                    f'Run with UPDATE_SNAPSHOT=1 to create it.')

    expected = snapshot_path.read_text()

    if generated_script != expected:
        import difflib
        diff = difflib.unified_diff(
            expected.splitlines(keepends=True),
            generated_script.splitlines(keepends=True),
            fromfile=f'{test_name}.sh (expected)',
            tofile=f'{test_name}.sh (actual)',
        )
        diff_text = ''.join(diff)
        pytest.fail(
            f'Generated script does not match snapshot: {snapshot_path}\n\n'
            f'Diff:\n{diff_text}\n\n'
            f'Run with UPDATE_SNAPSHOT=1 to update the snapshot.')


class TestSlurmProvisionTimeout:
    """Test conditional provision timeout logic in make_deploy_resources_variables.

    When auto-selecting across partitions (no zone specified), timeout is 600s
    to enable failover. When user specifies a partition, timeout is -1
    (unlimited). Config overrides always take precedence.
    """

    FAKE_SSH_CONFIG = {
        'hostname': '10.0.0.1',
        'port': '22',
        'user': 'slurm',
        'identityfile': ['/home/user/.ssh/id_rsa'],
    }

    def _make_deploy_vars(self, zone, config_return):
        """Call make_deploy_resources_variables with mocked resources."""
        cloud = slurm_cloud.Slurm()

        mock_resources = mock.MagicMock(unsafe=True)
        mock_resources.zone = zone
        mock_resources.instance_type = '4CPU--16GB'
        mock_resources.assert_launchable.return_value = mock_resources
        mock_resources.extract_docker_image.return_value = None

        region = mock.MagicMock()
        region.name = 'test-cluster'
        zones_list = [mock.MagicMock(name=zone)] if zone else None

        mock_ssh_config = mock.MagicMock()
        mock_ssh_config.lookup.return_value = self.FAKE_SSH_CONFIG

        with patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
                   return_value=config_return) as mock_config, \
             patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config',
                   return_value=mock_ssh_config), \
             patch('sky.clouds.slurm.slurm_utils.get_partitions',
                   return_value=['default', 'gpu', 'cpu']), \
             patch('sky.clouds.slurm.slurm_utils.get_gres_gpu_type',
                   side_effect=lambda cluster, t: t):
            deploy_vars = cloud.make_deploy_resources_variables(
                resources=mock_resources,
                cluster_name=mock.MagicMock(),
                region=region,
                zones=zones_list,
                num_nodes=1,
            )
        return deploy_vars, mock_config

    @pytest.mark.parametrize('zone,config_return,expected_timeout', [
        (None, None, 120),
        ('gpu', None, 86400),
        (None, 1800, 1800),
        ('gpu', 30, 30),
    ])
    def test_provision_timeout(self, zone, config_return, expected_timeout):
        """Test provision_timeout matches expected value."""
        deploy_vars, mock_config = self._make_deploy_vars(zone, config_return)
        assert deploy_vars['provision_timeout'] == expected_timeout
        mock_config.assert_called_once_with(cloud='slurm',
                                            region='test-cluster',
                                            keys=('provision_timeout',),
                                            default_value=None)


class TestProvisionTimeoutPassthrough:
    """Test that provision_timeout from provider_config is used in
    _create_virtual_instance."""

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.SSHCommandRunner')
    def test_default_timeout_passthrough(self, mock_ssh_runner,
                                         mock_slurm_client,
                                         mock_get_partition_info,
                                         mock_get_proctrack_type,
                                         mock_wait_for_job_nodes):
        """When provider_config has provision_timeout (default 120s),
        _wait_for_job_nodes receives that value."""
        from sky.adaptors.slurm import SlurmPartition
        from sky.provision import common

        mock_get_partition_info.return_value = SlurmPartition(name='gpu',
                                                              is_default=False,
                                                              maxtime=7 * 24 *
                                                              60 * 60)
        mock_get_proctrack_type.return_value = 'cgroup'

        mock_client = mock.MagicMock()
        mock_client.query_jobs.return_value = []
        mock_client.get_job_nodes.return_value = (['node1'], {
            'node1': '10.0.0.5'
        })
        mock_slurm_client.return_value = mock_client

        mock_runner = mock.MagicMock()
        mock_runner.run.return_value = (0, '', '')
        mock_runner.get_remote_home_dir.return_value = '/home/testuser'
        mock_ssh_runner.return_value = mock_runner

        config = common.ProvisionConfig(
            provider_config={
                'ssh': {
                    'hostname': 'login.example.com',
                    'port': '22',
                    'user': 'testuser',
                    'private_key': '/path/to/key',
                },
                'cluster': 'test-slurm',
                'partition': 'gpu',
                'provision_timeout': 120,
            },
            authentication_config={},
            docker_config={},
            node_config={
                'cpus': 2,
                'memory': 8,
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        slurm_instance._create_virtual_instance(
            region='us-west-2',
            cluster_name='test-timeout',
            cluster_name_on_cloud='test-timeout',
            config=config,
        )

        mock_wait_for_job_nodes.assert_called_once_with(mock_client, mock.ANY,
                                                        120, 'gpu', mock.ANY)
        # get_job_nodes should be called without wait params
        mock_client.get_job_nodes.assert_called_once_with(mock.ANY)

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.SSHCommandRunner')
    def test_explicit_timeout_passthrough(self, mock_ssh_runner,
                                          mock_slurm_client,
                                          mock_get_partition_info,
                                          mock_get_proctrack_type,
                                          mock_wait_for_job_nodes):
        """When provider_config has provision_timeout, it passes through."""
        from sky.adaptors.slurm import SlurmPartition
        from sky.provision import common

        mock_get_partition_info.return_value = SlurmPartition(name='gpu',
                                                              is_default=False,
                                                              maxtime=7 * 24 *
                                                              60 * 60)
        mock_get_proctrack_type.return_value = 'cgroup'

        mock_client = mock.MagicMock()
        mock_client.query_jobs.return_value = []
        mock_client.get_job_nodes.return_value = (['node1'], {
            'node1': '10.0.0.5'
        })
        mock_slurm_client.return_value = mock_client

        mock_runner = mock.MagicMock()
        mock_runner.run.return_value = (0, '', '')
        mock_runner.get_remote_home_dir.return_value = '/home/testuser'
        mock_ssh_runner.return_value = mock_runner

        config = common.ProvisionConfig(
            provider_config={
                'ssh': {
                    'hostname': 'login.example.com',
                    'port': '22',
                    'user': 'testuser',
                    'private_key': '/path/to/key',
                },
                'cluster': 'test-slurm',
                'partition': 'gpu',
                'provision_timeout': 120,
            },
            authentication_config={},
            docker_config={},
            node_config={
                'cpus': 2,
                'memory': 8,
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        slurm_instance._create_virtual_instance(
            region='us-west-2',
            cluster_name='test-timeout-explicit',
            cluster_name_on_cloud='test-timeout-explicit',
            config=config,
        )

        # provision_timeout=120 should be passed to _wait_for_job_nodes
        mock_wait_for_job_nodes.assert_called_once_with(mock_client, mock.ANY,
                                                        120, 'gpu', mock.ANY)
        # get_job_nodes should be called without wait params
        mock_client.get_job_nodes.assert_called_once_with(mock.ANY)


class TestCreateVirtualInstance:
    """Test slurm_instance._create_virtual_instance() script generation."""

    def _setup_mocks(self, mock_ssh_runner, mock_slurm_client,
                     mock_get_partition_info, partition_name):
        """Configure standard mocks for _create_virtual_instance tests."""
        from sky.adaptors.slurm import SlurmPartition

        mock_get_partition_info.return_value = SlurmPartition(
            name=partition_name, is_default=False, maxtime=7 * 24 * 60 * 60)

        mock_client = mock.MagicMock()
        mock_client.query_jobs.return_value = []
        mock_client.get_job_nodes.return_value = (['node1'], {
            'node1': '10.0.0.5'
        })
        mock_slurm_client.return_value = mock_client

        mock_runner = mock.MagicMock()
        mock_runner.run.return_value = (0, '', '')
        mock_runner.get_remote_home_dir.return_value = '/home/testuser'
        mock_ssh_runner.return_value = mock_runner

    def _run_and_capture_script(self, cluster_name, config):
        """Run _create_virtual_instance and capture the generated script."""
        written_script = None

        def capture_write(content):
            nonlocal written_script
            written_script = content

        with patch('tempfile.NamedTemporaryFile') as mock_tempfile:
            mock_file = mock.MagicMock()
            mock_file.__enter__.return_value = mock_file
            mock_file.write.side_effect = capture_write
            mock_tempfile.return_value = mock_file

            slurm_instance._create_virtual_instance(
                region='us-west-2',
                cluster_name=cluster_name,
                cluster_name_on_cloud=cluster_name,
                config=config,
            )

        assert written_script is not None, 'Script was not written'
        return written_script

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.SSHCommandRunner')
    def test_container_script_format(self, mock_ssh_runner, mock_slurm_client,
                                     mock_get_partition_info,
                                     mock_get_proctrack_type,
                                     mock_wait_for_job_nodes):
        """Test that sbatch provision script for containers is correct."""
        from sky.provision import common

        self._setup_mocks(mock_ssh_runner, mock_slurm_client,
                          mock_get_partition_info, 'gpu')
        mock_get_proctrack_type.return_value = 'cgroup'

        config = common.ProvisionConfig(
            provider_config={
                'ssh': {
                    'hostname': 'login.example.com',
                    'port': '22',
                    'user': 'testuser',
                    'private_key': '/path/to/key',
                },
                'cluster': 'test-slurm',
                'partition': 'gpu',
                'provision_timeout': 300,
            },
            authentication_config={},
            docker_config={},
            node_config={
                'cpus': 4,
                'memory': 16,
                'accelerator_type': 'A100',
                'accelerator_count': 2,
                'image_id': 'nvcr.io/nvidia/pytorch:24.01-py3',
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        written_script = self._run_and_capture_script('test-cluster', config)
        assert_sbatch_matches_snapshot('containers', written_script)

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.SSHCommandRunner')
    def test_non_container_script_format(self, mock_ssh_runner,
                                         mock_slurm_client,
                                         mock_get_partition_info,
                                         mock_get_proctrack_type,
                                         mock_wait_for_job_nodes):
        """Test that sbatch provision script without containers is correct."""
        from sky.provision import common

        self._setup_mocks(mock_ssh_runner, mock_slurm_client,
                          mock_get_partition_info, 'cpus')
        mock_get_proctrack_type.return_value = 'cgroup'

        config = common.ProvisionConfig(
            provider_config={
                'ssh': {
                    'hostname': 'login.example.com',
                    'port': '22',
                    'user': 'testuser',
                    'private_key': '/path/to/key',
                },
                'cluster': 'test-slurm',
                'partition': 'cpus',
                'provision_timeout': 300,
            },
            authentication_config={},
            docker_config={},
            node_config={
                'cpus': 2,
                'memory': 8,
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        written_script = self._run_and_capture_script(
            'test-cluster-no-container', config)
        assert_sbatch_matches_snapshot('basic', written_script)


class TestGetPendingJobCount:
    """Test SlurmClient.get_pending_job_count()."""

    @patch('sky.adaptors.slurm.SlurmClient._run_slurm_cmd')
    def test_basic_count(self, mock_run):
        """Test counting pending jobs in a partition."""
        mock_run.return_value = (0, '100\n101\n102\n', '')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
        count = client.get_pending_job_count('gpu')
        assert count == 3

    @patch('sky.adaptors.slurm.SlurmClient._run_slurm_cmd')
    def test_exclude_own_job(self, mock_run):
        """Test excluding our own job from the count."""
        mock_run.return_value = (0, '100\n101\n102\n', '')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
        count = client.get_pending_job_count('gpu', exclude_job_id='101')
        assert count == 2

    @patch('sky.adaptors.slurm.SlurmClient._run_slurm_cmd')
    def test_empty_queue(self, mock_run):
        """Test empty pending queue returns 0."""
        mock_run.return_value = (0, '', '')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
        count = client.get_pending_job_count('gpu')
        assert count == 0

    @patch('sky.adaptors.slurm.SlurmClient._run_slurm_cmd')
    def test_command_failure(self, mock_run):
        """Test that command failure returns -1."""
        mock_run.return_value = (1, '', 'error')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
        count = client.get_pending_job_count('gpu')
        assert count == -1

    @patch('sky.adaptors.slurm.SlurmClient._run_slurm_cmd')
    def test_only_own_job(self, mock_run):
        """Test that excluding our own job from single-entry queue returns 0."""
        mock_run.return_value = (0, '100\n', '')
        client = slurm.SlurmClient(is_inside_slurm_cluster=True)
        count = client.get_pending_job_count('gpu', exclude_job_id='100')
        assert count == 0


class TestOnPendingMessage:
    """Test the _on_pending callback message formatting."""

    @pytest.mark.parametrize('reason,pending_count,expected', [
        ('Resources', 12, 'Launching (pending: Resources, 12 others pending)'),
        ('Priority', 1, 'Launching (pending: Priority, 1 other pending)'),
        ('Resources', 0, 'Launching (pending: Resources)'),
        (None, 5, 'Launching (5 others pending)'),
        (None, None, 'Launching'),
        (None, 0, 'Launching'),
        ('Priority', None, 'Launching (pending: Priority)'),
    ])
    def test_on_pending_message_format(self, reason, pending_count, expected):
        """Test _on_pending callback formats messages correctly."""
        parts = []
        if reason:
            parts.append(f'pending: {reason}')
        if pending_count is not None and pending_count > 0:
            word = 'other' if pending_count == 1 else 'others'
            parts.append(f'{pending_count} {word} pending')
        if parts:
            msg = f'Launching ({", ".join(parts)})'
        else:
            msg = 'Launching'

        assert msg == expected


class TestProportionalResources:
    """Test slurm_utils.get_proportional_resources()."""

    # 192 CPUs, 786 GB RAM, 8 A10G GPUs
    node_8gpu = slurm.NodeInfo(node='gpu-node1',
                               state='idle',
                               gres='gpu:a10g:8',
                               cpus=192,
                               memory_gb=786,
                               partition='gpus')

    # 96 CPUs, 384 GB RAM, 4 H100 GPUs
    node_4gpu = slurm.NodeInfo(node='gpu-node2',
                               state='idle',
                               gres='gpu:H100:4',
                               cpus=96,
                               memory_gb=384,
                               partition='gpus')

    # CPU-only node (no GPUs)
    node_cpu_only = slurm.NodeInfo(node='cpu-node1',
                                   state='idle',
                                   gres='(null)',
                                   cpus=64,
                                   memory_gb=256,
                                   partition='cpus')

    # Heterogeneous partition: 2 GPU node (smaller)
    node_2gpu = slurm.NodeInfo(node='gpu-node3',
                               state='idle',
                               gres='gpu:a10g:2',
                               cpus=48,
                               memory_gb=196,
                               partition='gpus')

    # Default partition node (with '*')
    node_default_part = slurm.NodeInfo(node='gpu-node4',
                                       state='idle',
                                       gres='gpu:a10g:8',
                                       cpus=192,
                                       memory_gb=786,
                                       partition='default*')

    @pytest.mark.parametrize(
        'nodes,acc_type,acc_count,partition,expected',
        [
            # Full allocation: 8/8 GPUs on 192-CPU/786GB node
            ([node_8gpu], 'a10g', 8, None, (192.0, 786.0)),
            # Partial allocation: 2/8 GPUs → 48 CPUs, 196GB
            ([node_8gpu], 'a10g', 2, None,
             (float(math.floor(192 / 8 * 2)), float(math.floor(786 / 8 * 2)))),
            # Single GPU: 1/8 GPUs → 24 CPUs, 98GB
            ([node_8gpu], 'a10g', 1, None,
             (float(math.floor(192 / 8 * 1)), float(math.floor(786 / 8 * 1)))),
            # 4/8 GPUs → 96 CPUs, 393GB
            ([node_8gpu], 'a10g', 4, None,
             (float(math.floor(192 / 8 * 4)), float(math.floor(786 / 8 * 4)))),
            # No matching GPU type → None
            ([node_8gpu], 'H100', 2, None, None),
            # CPU-only nodes → None
            ([node_cpu_only], 'a10g', 1, None, None),
            # H100 node: 2/4 GPUs → 48 CPUs, 192GB
            ([node_4gpu], 'H100', 2, None,
             (float(math.floor(96 / 4 * 2)), float(math.floor(384 / 4 * 2)))),
            # Partition filtering: only considers matching partition
            ([node_8gpu, node_cpu_only], 'a10g', 2, 'gpus',
             (float(math.floor(192 / 8 * 2)), float(math.floor(786 / 8 * 2)))),
            # Partition filtering: no matching nodes in partition
            ([node_8gpu], 'a10g', 2, 'nonexistent', None),
            # Heterogeneous partition: uses node with max GPU count
            ([node_8gpu, node_2gpu], 'a10g', 2, None,
             (float(math.floor(192 / 8 * 2)), float(math.floor(786 / 8 * 2)))),
        ])
    @patch('sky.provision.slurm.utils.get_cluster_default_partition')
    @patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_get_proportional_resources(self, mock_get_nodes,
                                        mock_default_partition, nodes, acc_type,
                                        acc_count, partition, expected):
        """Test proportional resource computation for various scenarios."""
        mock_get_nodes.return_value = nodes
        mock_default_partition.return_value = 'default'

        result = slurm_utils.get_proportional_resources(
            'test-cluster',
            partition=partition,
            acc_type=acc_type,
            acc_count=acc_count)
        assert result == expected

    @patch('sky.provision.slurm.utils.get_cluster_default_partition')
    @patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_default_partition_star_handling(self, mock_get_nodes,
                                            mock_default_partition):
        """Test that default partition with '*' suffix is handled correctly."""
        mock_get_nodes.return_value = [self.node_default_part]
        mock_default_partition.return_value = 'default'

        result = slurm_utils.get_proportional_resources(
            'test-cluster',
            partition='default',
            acc_type='a10g',
            acc_count=2)
        assert result is not None
        assert result == (float(math.floor(192 / 8 * 2)),
                          float(math.floor(786 / 8 * 2)))

    @patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_node_info_query_failure(self, mock_get_nodes):
        """Test that node info query failures return None with warning."""
        mock_get_nodes.side_effect = Exception('SSH connection failed')

        result = slurm_utils.get_proportional_resources(
            'test-cluster', acc_type='a10g', acc_count=2)
        assert result is None

    @pytest.mark.parametrize(
        'nodes,cpus,partition,expected',
        [
            # 48/192 CPUs on 192-CPU/786GB node → 196GB
            ([node_8gpu], 48.0, None,
             (48.0, float(math.floor(786 / 192 * 48)))),
            # Full CPU allocation
            ([node_cpu_only], 64.0, None, (64.0, 256.0)),
            # Partial CPU on CPU-only node: 16/64 CPUs → 64GB
            ([node_cpu_only], 16.0, None,
             (16.0, float(math.floor(256 / 64 * 16)))),
            # Partition filtering
            ([node_cpu_only, node_8gpu], 16.0, 'cpus',
             (16.0, float(math.floor(256 / 64 * 16)))),
            # No nodes in partition
            ([node_cpu_only], 16.0, 'nonexistent', None),
            # Picks node with most CPUs
            ([node_cpu_only, node_8gpu], 16.0, None,
             (16.0, float(math.floor(786 / 192 * 16)))),
        ])
    @patch('sky.provision.slurm.utils.get_cluster_default_partition')
    @patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_cpu_only_proportional_memory(self, mock_get_nodes,
                                          mock_default_partition, nodes, cpus,
                                          partition, expected):
        """Test proportional memory for CPU-only jobs."""
        mock_get_nodes.return_value = nodes
        mock_default_partition.return_value = 'default'

        result = slurm_utils.get_proportional_resources(
            'test-cluster', partition=partition, cpus=cpus)
        assert result == expected


class TestMakeDeployResourcesWithProportional:
    """Test that make_deploy_resources_variables() uses proportional values."""

    @patch('sky.provision.slurm.utils.get_proportional_resources')
    @patch('sky.provision.slurm.utils.get_gres_gpu_type')
    @patch('sky.provision.slurm.utils.get_partitions')
    def test_proportional_values_used_when_no_user_override(
            self, mock_get_partitions, mock_get_gres, mock_get_proportional):
        """When user doesn't set CPU/memory, proportional values are used."""
        mock_get_partitions.return_value = ['gpus']
        mock_get_gres.return_value = 'a10g'
        mock_get_proportional.return_value = (48.0, 196.0)

        cloud = slurm_cloud.Slurm()
        # Instance type with defaults: 8 CPUs, 32GB, 2 A10G
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            instance_type='8CPU--32GB--a10g:2',
        )
        resources = resources.assert_launchable()

        region = mock.MagicMock()
        region.name = 'test-cluster'
        cluster_name = mock.MagicMock()

        with patch.object(slurm_utils, 'get_slurm_ssh_config') as mock_ssh:
            mock_ssh_obj = mock.MagicMock()
            mock_ssh_obj.lookup.return_value = {
                'hostname': 'login.example.com',
                'port': '22',
                'user': 'testuser',
                'identityfile': ['/path/to/key'],
            }
            mock_ssh.return_value = mock_ssh_obj

            deploy_vars = cloud.make_deploy_resources_variables(
                resources, cluster_name, region, None, 1)

        assert deploy_vars['cpus'] == '48.0'
        assert deploy_vars['memory'] == '196.0'

    @patch('sky.provision.slurm.utils.get_proportional_resources')
    @patch('sky.provision.slurm.utils.get_gres_gpu_type')
    @patch('sky.provision.slurm.utils.get_partitions')
    def test_user_overrides_preserved(self, mock_get_partitions, mock_get_gres,
                                      mock_get_proportional):
        """When user sets CPU and memory, proportional values are not used."""
        mock_get_partitions.return_value = ['gpus']
        mock_get_gres.return_value = 'a10g'
        mock_get_proportional.return_value = (48.0, 196.0)

        cloud = slurm_cloud.Slurm()
        # User explicitly sets cpus and memory
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            instance_type='16CPU--64GB--a10g:2',
            cpus='16',
            memory='64',
        )
        resources = resources.assert_launchable()

        region = mock.MagicMock()
        region.name = 'test-cluster'
        cluster_name = mock.MagicMock()

        with patch.object(slurm_utils, 'get_slurm_ssh_config') as mock_ssh:
            mock_ssh_obj = mock.MagicMock()
            mock_ssh_obj.lookup.return_value = {
                'hostname': 'login.example.com',
                'port': '22',
                'user': 'testuser',
                'identityfile': ['/path/to/key'],
            }
            mock_ssh.return_value = mock_ssh_obj

            deploy_vars = cloud.make_deploy_resources_variables(
                resources, cluster_name, region, None, 1)

        # User values should be preserved
        assert deploy_vars['cpus'] == '16.0'
        assert deploy_vars['memory'] == '64.0'

    @patch('sky.provision.slurm.utils.get_proportional_resources')
    @patch('sky.provision.slurm.utils.get_gres_gpu_type')
    @patch('sky.provision.slurm.utils.get_partitions')
    def test_partial_user_override(self, mock_get_partitions, mock_get_gres,
                                   mock_get_proportional):
        """When user sets only CPU, memory uses proportional value."""
        mock_get_partitions.return_value = ['gpus']
        mock_get_gres.return_value = 'a10g'
        mock_get_proportional.return_value = (48.0, 196.0)

        cloud = slurm_cloud.Slurm()
        # User only sets cpus, not memory
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            instance_type='16CPU--32GB--a10g:2',
            cpus='16',
        )
        resources = resources.assert_launchable()

        region = mock.MagicMock()
        region.name = 'test-cluster'
        cluster_name = mock.MagicMock()

        with patch.object(slurm_utils, 'get_slurm_ssh_config') as mock_ssh:
            mock_ssh_obj = mock.MagicMock()
            mock_ssh_obj.lookup.return_value = {
                'hostname': 'login.example.com',
                'port': '22',
                'user': 'testuser',
                'identityfile': ['/path/to/key'],
            }
            mock_ssh.return_value = mock_ssh_obj

            deploy_vars = cloud.make_deploy_resources_variables(
                resources, cluster_name, region, None, 1)

        # User-set CPU preserved, memory uses proportional
        assert deploy_vars['cpus'] == '16.0'
        assert deploy_vars['memory'] == '196.0'

    @patch('sky.provision.slurm.utils.get_proportional_resources')
    @patch('sky.provision.slurm.utils.get_gres_gpu_type')
    @patch('sky.provision.slurm.utils.get_partitions')
    def test_cpu_only_proportional_memory_in_deploy(self, mock_get_partitions,
                                                    mock_get_gres,
                                                    mock_get_proportional):
        """CPU-only instances use proportional memory based on CPU fraction."""
        mock_get_partitions.return_value = ['cpus']
        mock_get_proportional.return_value = (4.0, 64.0)

        cloud = slurm_cloud.Slurm()
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            instance_type='4CPU--16GB',
        )
        resources = resources.assert_launchable()

        region = mock.MagicMock()
        region.name = 'test-cluster'
        cluster_name = mock.MagicMock()

        with patch.object(slurm_utils, 'get_slurm_ssh_config') as mock_ssh:
            mock_ssh_obj = mock.MagicMock()
            mock_ssh_obj.lookup.return_value = {
                'hostname': 'login.example.com',
                'port': '22',
                'user': 'testuser',
                'identityfile': ['/path/to/key'],
            }
            mock_ssh.return_value = mock_ssh_obj

            deploy_vars = cloud.make_deploy_resources_variables(
                resources, cluster_name, region, None, 1)

        mock_get_proportional.assert_called_once_with(
            'test-cluster', partition='cpus', cpus=4.0)
        assert deploy_vars['cpus'] == '4.0'
        assert deploy_vars['memory'] == '64.0'

    @patch('sky.provision.slurm.utils.get_proportional_resources')
    @patch('sky.provision.slurm.utils.get_gres_gpu_type')
    @patch('sky.provision.slurm.utils.get_partitions')
    def test_cpu_only_user_memory_override(self, mock_get_partitions,
                                           mock_get_gres,
                                           mock_get_proportional):
        """CPU-only with user-set memory skips proportional."""
        mock_get_partitions.return_value = ['cpus']

        cloud = slurm_cloud.Slurm()
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            instance_type='4CPU--16GB',
            memory='16',
        )
        resources = resources.assert_launchable()

        region = mock.MagicMock()
        region.name = 'test-cluster'
        cluster_name = mock.MagicMock()

        with patch.object(slurm_utils, 'get_slurm_ssh_config') as mock_ssh:
            mock_ssh_obj = mock.MagicMock()
            mock_ssh_obj.lookup.return_value = {
                'hostname': 'login.example.com',
                'port': '22',
                'user': 'testuser',
                'identityfile': ['/path/to/key'],
            }
            mock_ssh.return_value = mock_ssh_obj

            deploy_vars = cloud.make_deploy_resources_variables(
                resources, cluster_name, region, None, 1)

        mock_get_proportional.assert_not_called()
        assert deploy_vars['cpus'] == '4.0'
        assert deploy_vars['memory'] == '16.0'

    @patch('sky.provision.slurm.utils.get_proportional_resources')
    @patch('sky.provision.slurm.utils.get_gres_gpu_type')
    @patch('sky.provision.slurm.utils.get_partitions')
    def test_proportional_returns_none_fallback(self, mock_get_partitions,
                                                mock_get_gres,
                                                mock_get_proportional):
        """When proportional returns None, instance type defaults are used."""
        mock_get_partitions.return_value = ['gpus']
        mock_get_gres.return_value = 'a10g'
        mock_get_proportional.return_value = None

        cloud = slurm_cloud.Slurm()
        resources = resources_lib.Resources(
            cloud=slurm_cloud.Slurm(),
            instance_type='8CPU--32GB--a10g:2',
        )
        resources = resources.assert_launchable()

        region = mock.MagicMock()
        region.name = 'test-cluster'
        cluster_name = mock.MagicMock()

        with patch.object(slurm_utils, 'get_slurm_ssh_config') as mock_ssh:
            mock_ssh_obj = mock.MagicMock()
            mock_ssh_obj.lookup.return_value = {
                'hostname': 'login.example.com',
                'port': '22',
                'user': 'testuser',
                'identityfile': ['/path/to/key'],
            }
            mock_ssh.return_value = mock_ssh_obj

            deploy_vars = cloud.make_deploy_resources_variables(
                resources, cluster_name, region, None, 1)

        # Falls back to instance type defaults
        assert deploy_vars['cpus'] == '8.0'
        assert deploy_vars['memory'] == '32.0'

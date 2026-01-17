"""Tests for Slurm cloud implementation."""

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


class TestCreateVirtualInstance:
    """Test slurm_instance._create_virtual_instance() script generation."""

    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.SSHCommandRunner')
    def test_container_script_format(self, mock_ssh_runner, mock_slurm_client,
                                     mock_get_partition_info):
        """Test that sbatch provision script stays host-native."""
        from sky.adaptors.slurm import SlurmPartition
        from sky.provision import common

        # Mock get_partition_info to return a valid partition
        mock_get_partition_info.return_value = SlurmPartition(
            name='gpu',
            is_default=False,
            maxtime=7 * 24 * 60 * 60,  # 7 days in seconds
        )

        # Mock the SlurmClient
        mock_client = mock.MagicMock()
        mock_client.query_jobs.return_value = []  # No existing jobs
        mock_client.submit_sbatch_script.return_value = '12345'  # job_id
        mock_client.get_job_nodes.return_value = (['node1'], {
            'node1': '10.0.0.5'
        })
        mock_slurm_client.return_value = mock_client

        # Mock the SSH runner's run and rsync methods
        mock_runner_instance = mock.MagicMock()

        def mock_run_side_effect(cmd, **kwargs):
            require_outputs = kwargs.get('require_outputs', False)
            if 'SKYPILOT_HOME_DIR' in cmd and require_outputs:
                return (0, 'SKYPILOT_HOME_DIR: /home/testuser', '')
            elif require_outputs:
                return (0, '', '')
            else:
                return 0

        mock_runner_instance.run.side_effect = mock_run_side_effect
        mock_runner_instance.rsync.return_value = None
        mock_runner_instance.get_remote_home_dir.return_value = '/home/testuser'
        mock_ssh_runner.return_value = mock_runner_instance

        cluster_name = 'test-cluster'
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

        # Capture the sbatch script written to the temp file
        written_script = None

        def capture_write(content):
            nonlocal written_script
            written_script = content

        with patch('tempfile.NamedTemporaryFile') as mock_tempfile:
            mock_file = mock.MagicMock()
            mock_file.name = '/tmp/test_script.sh'
            mock_file.__enter__.return_value = mock_file

            # Patch the write method to capture the script
            mock_file.write.side_effect = capture_write
            mock_tempfile.return_value = mock_file

            slurm_instance._create_virtual_instance(
                region='us-west-2',
                cluster_name_on_cloud=cluster_name,
                config=config,
            )

        # Verify the script was written
        assert written_script is not None, "Script was not written"

        # Expected script - exact match (using absolute paths)
        container_image = 'nvcr.io/nvidia/pytorch:24.01-py3'
        container_name = cluster_name  # pyxis_container_name just returns cluster_name
        enroot_container_name = f'pyxis_{container_name}'
        remote_home_dir = '/home/testuser'
        sky_home_dir = f'{remote_home_dir}/.sky_clusters/{cluster_name}'
        skypilot_runtime_dir = f'/tmp/{cluster_name}'
        container_mounts = ','.join([
            f'{remote_home_dir}:{remote_home_dir}',
            f'{sky_home_dir}/sky_logs:/root/sky_logs',
            f'{sky_home_dir}/sky_workdir:/root/sky_workdir',
        ])
        ready_signal = f'{sky_home_dir}/.sky_sbatch_ready'
        expected_script = f"""#!/bin/bash
#SBATCH --job-name={cluster_name}
#SBATCH --output=.sky_provision/slurm-%j.out
#SBATCH --error=.sky_provision/slurm-%j.out
#SBATCH --nodes=1
#SBATCH --time=7-00:00:00
#SBATCH --wait-all-nodes=1
# Let the job be terminated rather than requeued implicitly.
#SBATCH --no-requeue
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --gres=gpu:A100:2

# Cleanup function to remove cluster dirs on job termination.
cleanup() {{
    # The Skylet is daemonized, so it is not automatically terminated when
    # the Slurm job is terminated, we need to kill it manually.
    echo "Terminating Skylet..."
    if [ -f "{skypilot_runtime_dir}/.sky/skylet_pid" ]; then
        kill $(cat "{skypilot_runtime_dir}/.sky/skylet_pid") 2>/dev/null || true
    fi
    echo "Cleaning up sky directories..."
    # Remove the per-node enroot container, if it exists.
    # This is only needed when container_scope=global.
    # When container_scope=job, named containers are removed automatically
    # at the end of the Slurm job, see: https://github.com/NVIDIA/pyxis/wiki/Setup#slurm-epilog
    srun --nodes=1 --ntasks-per-node=1 enroot remove -f {enroot_container_name} 2>/dev/null || true
    # Clean up sky runtime directory on each node.
    # NOTE: We can do this because --nodes for both this srun and the
    # sbatch is the same number. Otherwise, there are no guarantees
    # that this srun will run on the same subset of nodes as the srun
    # that created the sky directories.
    srun --nodes=1 rm -rf {skypilot_runtime_dir}
    rm -rf {sky_home_dir}
}}
trap cleanup TERM

# Create sky home directory and subdirectories for the cluster.
mkdir -p {sky_home_dir}/sky_logs {sky_home_dir}/sky_workdir {sky_home_dir}/.sky
# Create sky runtime directory on each node.
srun --nodes=1 mkdir -p {skypilot_runtime_dir}
# Marker file to indicate we're in a Slurm cluster.
touch {sky_home_dir}/.sky_slurm_cluster
# Suppress login messages.
touch {sky_home_dir}/.hushlogin
echo "Initializing container {container_name} on all nodes..."
srun --overlap --nodes=1 --ntasks-per-node=1 --container-image={container_image} --container-name={container_name}:create --container-mounts="{container_mounts}" --container-remap-root --no-container-mount-home --container-writable bash -c 'set -e
PACKAGES="ca-certificates rsync curl git wget fuse"
if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y $PACKAGES
elif command -v yum >/dev/null 2>&1; then
    yum install -y $PACKAGES fuse-libs
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y $PACKAGES fuse-libs
elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache $PACKAGES
fi
touch {sky_home_dir}/.sky_slurm_container {ready_signal} && sleep infinity' &
# ready_signal touched inside container
wait
"""

        assert written_script == expected_script, (
            f"Script mismatch.\n\n"
            f"=== EXPECTED ===\n{expected_script}\n\n"
            f"=== ACTUAL ===\n{written_script}\n\n"
            f"=== DIFF ===\n"
            f"Expected lines: {len(expected_script.splitlines())}\n"
            f"Actual lines: {len(written_script.splitlines())}\n")

    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.SSHCommandRunner')
    def test_non_container_script_format(self, mock_ssh_runner,
                                         mock_slurm_client,
                                         mock_get_partition_info):
        """Test that non-container sbatch script is properly formatted."""
        from sky.adaptors.slurm import SlurmPartition
        from sky.provision import common

        mock_get_partition_info.return_value = SlurmPartition(
            name='cpus',
            is_default=False,
            maxtime=7 * 24 * 60 * 60,
        )

        # Mock the SlurmClient
        mock_client = mock.MagicMock()
        mock_client.query_jobs.return_value = []
        mock_client.submit_sbatch_script.return_value = '12346'
        mock_client.get_job_nodes.return_value = (['node1'], {
            'node1': '10.0.0.5'
        })
        mock_slurm_client.return_value = mock_client

        # Mock the SSH runner
        mock_runner_instance = mock.MagicMock()

        def mock_run_side_effect(cmd, **kwargs):
            require_outputs = kwargs.get('require_outputs', False)
            if 'SKYPILOT_HOME_DIR' in cmd and require_outputs:
                return (0, 'SKYPILOT_HOME_DIR: /home/testuser', '')
            elif require_outputs:
                return (0, '', '')
            else:
                return 0

        mock_runner_instance.run.side_effect = mock_run_side_effect
        mock_runner_instance.rsync.return_value = None
        mock_runner_instance.get_remote_home_dir.return_value = '/home/testuser'
        mock_ssh_runner.return_value = mock_runner_instance

        cluster_name = 'test-cluster-no-container'
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

        written_script = None

        def capture_write(content):
            nonlocal written_script
            written_script = content

        with patch('tempfile.NamedTemporaryFile') as mock_tempfile:
            mock_file = mock.MagicMock()
            mock_file.name = '/tmp/test_script.sh'
            mock_file.__enter__.return_value = mock_file
            mock_file.write.side_effect = capture_write
            mock_tempfile.return_value = mock_file

            slurm_instance._create_virtual_instance(
                region='us-west-2',
                cluster_name_on_cloud=cluster_name,
                config=config,
            )

        assert written_script is not None

        # Verify NO container directives present
        assert '--container-image' not in written_script
        assert '--container-name' not in written_script
        assert '--container-mounts' not in written_script

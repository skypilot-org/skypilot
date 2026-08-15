"""Tests for Slurm cloud implementation."""

import os
from pathlib import Path
import re
from unittest.mock import patch
import unittest.mock as mock

import pytest

from sky import resources as resources_lib
from sky.adaptors import slurm
from sky.clouds import slurm as slurm_cloud
from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm import utils as slurm_utils
from sky.skylet import constants


class TestGetSubmitUser:

    def test_config_is_server_managed(self):
        key = ('slurm', 'submit_as_user')
        assert key in constants.SKIPPED_CLIENT_OVERRIDE_KEYS

    @pytest.mark.parametrize('user_name,expected', [
        ('alice@example.com', 'alice'),
        ('alice', 'alice'),
        ('alice.ml@example.com', 'alice.ml'),
    ])
    @patch('sky.provision.slurm.utils.common_utils.get_current_user_name')
    @patch('sky.provision.slurm.utils.skypilot_config.'
           'get_effective_region_config')
    def test_enabled(self, mock_get_config, mock_get_user_name, user_name,
                     expected):
        mock_get_config.return_value = True
        mock_get_user_name.return_value = user_name

        assert slurm_utils.get_submit_user('my-cluster') == expected
        mock_get_config.assert_called_once_with(cloud='slurm',
                                                region='my-cluster',
                                                keys=('submit_as_user',),
                                                default_value=False)

    @patch('sky.provision.slurm.utils.common_utils.get_current_user_name')
    @patch('sky.provision.slurm.utils.skypilot_config.'
           'get_effective_region_config')
    def test_disabled(self, mock_get_config, mock_get_user_name):
        mock_get_config.return_value = False

        assert slurm_utils.get_submit_user('my-cluster') is None
        mock_get_user_name.assert_not_called()

    @pytest.mark.parametrize('user_name', [
        '@example.com',
        'Alice@example.com',
        'alice+ml@example.com',
        '-alice@example.com',
    ])
    @patch('sky.provision.slurm.utils.common_utils.get_current_user_name')
    @patch('sky.provision.slurm.utils.skypilot_config.'
           'get_effective_region_config',
           return_value=True)
    def test_invalid_unix_user_rejected(self, _mock_get_config,
                                        mock_get_user_name, user_name):
        mock_get_user_name.return_value = user_name

        with pytest.raises(ValueError, match='valid Unix user'):
            slurm_utils.get_submit_user('my-cluster')


class TestSlurmClientSubmitUser:

    @patch('sky.adaptors.slurm.command_runner.SlurmLoginNodeCommandRunner')
    def test_passes_submit_user_to_login_runner(self, mock_runner_class):
        runner = mock_runner_class.return_value
        runner.run.return_value = (0, '', '')
        runner.get_remote_home_dir.return_value = '/home/alice'

        client = slurm.SlurmClient(ssh_host='login.example.com',
                                   ssh_port=22,
                                   ssh_user='ubuntu',
                                   ssh_key='/root/.ssh/key',
                                   slurm_user='alice')

        assert client.get_remote_home_dir() == '/home/alice'
        mock_runner_class.assert_called_once_with(('login.example.com', 22),
                                                  'ubuntu',
                                                  '/root/.ssh/key',
                                                  ssh_proxy_command=None,
                                                  ssh_proxy_jump=None,
                                                  enable_interactive_auth=True,
                                                  disable_identities_only=True,
                                                  slurm_user='alice')


class TestSubmitUserDeployVariables:

    @staticmethod
    def _make_deploy_variables(submit_user,
                               ssh_user='root',
                               volume_mounts=None,
                               image_id=None,
                               config_container_mounts=None):
        cloud = slurm_cloud.Slurm()
        resources = mock.MagicMock(unsafe=True)
        resources.zone = 'gpu'
        resources.instance_type = '4CPU--16GB'
        resources.assert_launchable.return_value = resources
        resources.extract_docker_image.return_value = image_id
        resources.cluster_config_overrides = {}

        region = mock.MagicMock()
        region.name = 'my-cluster'
        zone = mock.MagicMock()
        zone.name = 'gpu'
        ssh_config = mock.MagicMock()
        ssh_config.lookup.return_value = {
            'hostname': 'login.example.com',
            'port': '22',
            'user': ssh_user,
            'identityfile': ['/root/.ssh/id_ed25519'],
        }

        def _get_nested(keys, default_value=None, **_):
            if (keys == ('slurm', 'cluster_configs', 'my-cluster',
                         'container_mounts') and
                    config_container_mounts is not None):
                return config_container_mounts
            return default_value

        with patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config',
                   return_value=ssh_config), \
             patch('sky.clouds.slurm.slurm_utils.get_submit_user',
                   return_value=submit_user), \
             patch('sky.clouds.slurm.slurm_utils.get_partitions',
                   return_value=['gpu']), \
             patch('sky.clouds.slurm.skypilot_config.get_nested',
                   side_effect=_get_nested), \
             patch('sky.clouds.slurm.skypilot_config.'
                   'get_effective_region_config', return_value=None):
            return cloud.make_deploy_resources_variables(
                resources=resources,
                cluster_name=mock.MagicMock(),
                region=region,
                zones=[zone],
                num_nodes=1,
                volume_mounts=volume_mounts)

    def test_submit_user_persisted(self):
        deploy_vars = self._make_deploy_variables('alice')

        assert deploy_vars['slurm_user'] == 'alice'

    def test_disabled_keeps_default(self):
        deploy_vars = self._make_deploy_variables(None, ssh_user='ubuntu')

        assert deploy_vars['slurm_user'] is None

    def test_submit_user_allows_passwordless_sudo_transport(self):
        deploy_vars = self._make_deploy_variables('alice', ssh_user='ubuntu')

        assert deploy_vars['ssh_user'] == 'ubuntu'
        assert deploy_vars['slurm_user'] == 'alice'

    def test_volume_mounts_serialized(self):
        mount = mock.MagicMock()
        mount.volume_config.config = {'host_path': '/host/data'}
        mount.to_yaml_config.return_value = {
            'path': '/data',
            'volume_name': '',
            'volume_config': {
                'config': {
                    'host_path': '/host/data',
                    'mode': 'ro',
                },
            },
            'is_ephemeral': False,
        }

        deploy_vars = self._make_deploy_variables(None,
                                                  volume_mounts=[mount],
                                                  image_id='ubuntu:22.04')

        assert deploy_vars['slurm_volume_mounts'] == [
            mount.to_yaml_config.return_value
        ]

    @pytest.mark.parametrize(
        'config,image_id,error',
        [
            ({}, 'ubuntu:22.04', r"only supports inline host_path.*'/mnt'"),
            ({
                'host_path': '/host/data'
            }, None, 'require a container image'),
        ],
    )
    def test_volume_mounts_validate_slurm_support(self, config, image_id,
                                                  error):
        mount = mock.MagicMock()
        mount.path = '/mnt'
        mount.volume_config.config = config

        with pytest.raises(ValueError, match=error):
            self._make_deploy_variables(None,
                                        volume_mounts=[mount],
                                        image_id=image_id)

    def test_config_container_mounts_serialized(self):
        deploy_vars = self._make_deploy_variables(
            None,
            image_id='ubuntu:22.04',
            config_container_mounts={
                '/blob': '/data/blob',
                '/scratch': {
                    'host_path': '/nvme/$SLURM_JOB_ID',
                    'mode': 'rw',
                },
            })

        mounts = {
            m['path']: m['volume_config']['config']
            for m in deploy_vars['slurm_volume_mounts']
        }
        assert mounts == {
            '/blob': {
                'host_path': '/data/blob',
                'mode': 'ro',
            },
            '/scratch': {
                'host_path': '/nvme/$SLURM_JOB_ID',
                'mode': 'rw',
            },
        }

    def test_config_container_mounts_yield_to_task_volume(self):
        mount = mock.MagicMock()
        mount.path = '/data'
        mount.volume_config.config = {'host_path': '/host/data'}
        mount.to_yaml_config.return_value = {
            'path': '/data',
            'volume_config': {
                'config': {
                    'host_path': '/host/data',
                    'mode': 'ro',
                },
            },
        }

        deploy_vars = self._make_deploy_variables(
            None,
            volume_mounts=[mount],
            image_id='ubuntu:22.04',
            config_container_mounts={'/data': '/config/data'})

        assert deploy_vars['slurm_volume_mounts'] == [
            mount.to_yaml_config.return_value
        ]

    def test_config_container_mounts_skipped_without_image(self):
        deploy_vars = self._make_deploy_variables(
            None, config_container_mounts={'/blob': '/data/blob'})

        assert deploy_vars['slurm_volume_mounts'] == []


class TestSubmitUserTemplate:

    @pytest.mark.parametrize(
        'slurm_user,transport_user,image_id,expected_ssh_user', [
            ('alice', 'root', None, 'alice'),
            ('alice', 'root', 'ubuntu:22.04', 'root'),
            (None, 'ubuntu', None, 'ubuntu'),
        ])
    def test_persists_submit_user(self, tmp_path, slurm_user, transport_user,
                                  image_id, expected_ssh_user):
        from sky.utils import common_utils
        from sky.utils import yaml_utils

        output_path = tmp_path / 'cluster.yaml'
        common_utils.fill_template(
            'slurm-ray.yml.j2', {
                'slurm_user': slurm_user,
                'ssh_user': transport_user,
                'image_id': image_id,
                'cluster_name_on_cloud': 'test-cluster',
                'num_nodes': 1,
                'slurm_cluster': 'my-cluster',
                'slurm_partition': 'gpu',
                'provision_timeout': 120,
                'ssh_hostname': 'login.example.com',
                'ssh_port': 22,
                'slurm_private_key': '/root/.ssh/key',
                'slurm_proxy_command': None,
                'slurm_proxy_jump': None,
                'slurm_identities_only': True,
                'ssh_private_key': '/tmp/sky-key',
                'instance_type': '4CPU--16GB',
                'disk_size': 256,
                'cpus': 4,
                'memory': 16,
                'accelerator_type': None,
                'accelerator_count': 0,
                'sbatch_options': {},
                'slurm_volume_mounts': [{
                    'path': '/data',
                    'volume_name': '',
                    'volume_config': {
                        'config': {
                            'host_path': '/host/data',
                            'mode': 'ro',
                        },
                    },
                    'is_ephemeral': False,
                }],
                'sky_ray_yaml_remote_path': '/tmp/ray.yaml',
                'sky_ray_yaml_local_path': '/tmp/ray.yaml',
                'sky_remote_path': '/tmp/sky',
                'sky_wheel_hash': 'wheel.whl',
                'sky_local_path': '/tmp/sky.whl',
                'credentials': {},
                'initial_setup_commands': [],
                'slurm_sshd_host_key_filename': 'skypilot_host_key',
                'slurm_cluster_name_env_var': 'SKYPILOT_CLUSTER_NAME',
                'setup_sky_dirs_commands': '',
                'conda_installation_commands': '',
                'uv_installation_commands': '',
                'skypilot_wheel_installation_commands': '',
                'copy_skypilot_templates_commands': '',
            }, str(output_path))

        config = yaml_utils.read_yaml(str(output_path))
        if slurm_user is None:
            assert 'slurm_user' not in config['provider']
        else:
            assert config['provider']['slurm_user'] == slurm_user
        assert config['provider']['ssh']['user'] == transport_user
        assert config['auth']['ssh_user'] == expected_ssh_user
        assert config['available_node_types']['ray_head_default'][
            'node_config']['volume_mounts'][0]['path'] == '/data'


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
             ], '64CPU--256GB--h100:4', None, False, 'No GPU nodes matching'),
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


class TestRegionsWithOfferingPartitionMap:
    """Test configured partition validation against live partitions."""

    @patch('sky.clouds.slurm.slurm_utils.lookup_gpu_partition_map',
           new=mock.Mock(return_value=['configured-gpu']))
    @patch('sky.clouds.slurm.slurm_utils.get_partitions',
           new=mock.Mock(return_value=['live-gpu', 'cpu']))
    @patch.object(slurm_cloud.Slurm,
                  'existing_allowed_clusters',
                  new=mock.Mock(return_value=['cluster-a']))
    def test_fixed_region_partition_mismatch_returns_no_regions(self):
        regions = slurm_cloud.Slurm.regions_with_offering(
            instance_type='64CPU--256GB--H100:1',
            accelerators=None,
            use_spot=False,
            region='cluster-a',
            zone=None)

        assert regions == []

    @patch('sky.clouds.slurm.slurm_utils.lookup_gpu_partition_map',
           new=mock.Mock(return_value=['configured-gpu']))
    @patch('sky.clouds.slurm.slurm_utils.get_partitions',
           new=mock.Mock(return_value=['live-gpu', 'cpu']))
    @patch.object(slurm_cloud.Slurm,
                  'existing_allowed_clusters',
                  new=mock.Mock(return_value=['cluster-a']))
    def test_fixed_region_partition_mismatch_surfaces_hint(self):
        """The diagnostic must flow through the FeasibleResources hint, not
        an exception, so other any_of/ordered candidates stay eligible."""
        resources = mock.MagicMock(unsafe=True)
        resources.instance_type = '64CPU--256GB--H100:1'
        resources.region = 'cluster-a'
        resources.zone = None
        resources.use_spot = False
        resources.is_launchable.return_value = True
        resources.get_required_cloud_features.return_value = set()

        feasible = slurm_cloud.Slurm()._get_feasible_launchable_resources(
            resources)

        assert feasible.resources_list == []
        assert re.search(
            r"gpu_partition_map.*'H100'.*'cluster-a'.*cpu.*live-gpu",
            feasible.hint)

    @patch('sky.clouds.slurm.slurm_utils.check_instance_fits',
           new=mock.Mock(return_value=(True, None)))
    @patch('sky.clouds.slurm.slurm_utils.lookup_gpu_partition_map',
           new=mock.Mock(return_value=['gpu']))
    @patch('sky.clouds.slurm.slurm_utils.get_partitions',
           new=mock.Mock(side_effect=lambda cluster: {
               'cluster-a': ['cpu'],
               'cluster-b': ['gpu'],
           }[cluster]))
    @patch.object(slurm_cloud.Slurm,
                  'existing_allowed_clusters',
                  new=mock.Mock(return_value=['cluster-a', 'cluster-b']))
    def test_unspecified_region_continues_after_partition_mismatch(self):
        regions = slurm_cloud.Slurm.regions_with_offering(
            instance_type='64CPU--256GB--H100:1',
            accelerators=None,
            use_spot=False,
            region=None,
            zone=None)

        assert [region.name for region in regions] == ['cluster-b']


class TestLookupGpuPartitionMap:
    """Test slurm_utils.lookup_gpu_partition_map()."""

    @pytest.mark.parametrize(
        'gpu_partition_map,acc_type,expected',
        [
            # None map returns None
            (None, 'H100', None),
            # Exact match (string value -> list)
            ({
                'H100': 'h100-partition'
            }, 'H100', ['h100-partition']),
            # Case-insensitive match
            ({
                'h100': 'h100-partition'
            }, 'H100', ['h100-partition']),
            ({
                'H100': 'h100-partition'
            }, 'h100', ['h100-partition']),
            # Array value (passthrough)
            ({
                'H100': ['h100-train', 'h100-dev']
            }, 'H100', ['h100-train', 'h100-dev']),
            # Not found
            ({
                'H100': 'h100-partition'
            }, 'A100', None),
            # Empty map
            ({}, 'H100', None),
        ])
    @patch('sky.provision.slurm.utils.skypilot_config.'
           'get_effective_region_config')
    def test_lookup_gpu_partition_map(self, mock_get_effective,
                                      gpu_partition_map, acc_type, expected):
        mock_get_effective.return_value = gpu_partition_map
        result = slurm_utils.lookup_gpu_partition_map('my-cluster', acc_type)
        assert result == expected
        mock_get_effective.assert_called_once_with(cloud='slurm',
                                                   keys=('gpu_partition_map',),
                                                   region='my-cluster',
                                                   merge_dicts=True)


class TestLookupCpuPartition:
    """Test slurm_utils.lookup_cpu_partition()."""

    @pytest.mark.parametrize('config_value,expected', [
        (None, None),
        ('cpu-partition', 'cpu-partition'),
        ('batch', 'batch'),
    ])
    @patch('sky.provision.slurm.utils.skypilot_config.'
           'get_effective_region_config')
    def test_lookup_cpu_partition(self, mock_get_effective, config_value,
                                  expected):
        mock_get_effective.return_value = config_value
        result = slurm_utils.lookup_cpu_partition('my-cluster')
        assert result == expected
        mock_get_effective.assert_called_once_with(cloud='slurm',
                                                   keys=('cpu_partition',),
                                                   region='my-cluster',
                                                   default_value=None)


class TestCheckInstanceFitsWithGpuPartitionMap:
    """Test check_instance_fits with gpu_partition_map configured."""

    # Node with typeless GRES (gpu:8, no GPU type name)
    node_gpu_typeless = slurm.NodeInfo(node='gpu-node1',
                                       state='idle',
                                       gres='gpu:8',
                                       cpus=192,
                                       memory_gb=786,
                                       partition='h100')

    @pytest.mark.parametrize(
        'nodes,instance_type,partition,gpu_partition_map,'
        'expected_fits,reason_contains',
        [
            # GPU type in map, typeless GRES nodes -> should fit
            ([node_gpu_typeless], '64CPU--256GB--H100:4', 'h100', {
                'H100': 'h100'
            }, True, None),
            # GPU type in map but requesting too many GPUs -> should not fit
            ([node_gpu_typeless], '64CPU--256GB--H100:16', 'h100', {
                'H100': 'h100'
            }, False, 'gpu_partition_map'),
            # GPU type NOT in map -> falls back to normal resolution (fails
            # because gres is typeless)
            ([node_gpu_typeless], '64CPU--256GB--A100:4', 'h100', {
                'H100': 'h100'
            }, False, 'No GPU nodes matching'),
        ])
    @patch(
        'sky.provision.slurm.utils.skypilot_config.get_effective_region_config')
    @patch('sky.provision.slurm.utils.kv_cache.get_cache_entry',
           return_value=None)
    @patch('sky.provision.slurm.utils.get_cluster_default_partition')
    @patch('sky.provision.slurm.utils.slurm.SlurmClient')
    @patch('sky.provision.slurm.utils.SSHConfig.from_path')
    def test_check_instance_fits_with_map(
            self, mock_ssh_config, mock_slurm_client, mock_default_partition,
            mock_kv_cache, mock_get_effective, nodes, instance_type, partition,
            gpu_partition_map, expected_fits, reason_contains):
        """Test check_instance_fits with gpu_partition_map configured."""
        mock_default_partition.return_value = 'default'
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

        mock_get_effective.return_value = gpu_partition_map

        fits, reason = slurm_utils.check_instance_fits(
            cluster='test-cluster',
            instance_type=instance_type,
            partition=partition)

        assert fits is expected_fits
        if reason_contains:
            assert reason_contains in reason
        else:
            assert reason is None


class TestGresDirectiveGeneration:
    """Test GRES directive generation in sbatch scripts."""

    def test_typed_gres(self):
        """Typed GRES: gpu:type:count."""
        accelerator_type = 'NVIDIA_H100_80GB_HBM3'
        accelerator_count = 4
        if accelerator_count > 0:
            if (accelerator_type is not None and
                    accelerator_type.upper() != 'NONE'):
                directive = (f'#SBATCH --gres=gpu:{accelerator_type}:'
                             f'{accelerator_count}')
            else:
                directive = f'#SBATCH --gres=gpu:{accelerator_count}'
        assert directive == '#SBATCH --gres=gpu:NVIDIA_H100_80GB_HBM3:4'

    def test_typeless_gres(self):
        """Typeless GRES: gpu:count (used with gpu_partition_map)."""
        accelerator_type = None
        accelerator_count = 2
        if accelerator_count > 0:
            if (accelerator_type is not None and
                    accelerator_type.upper() != 'NONE'):
                directive = (f'#SBATCH --gres=gpu:{accelerator_type}:'
                             f'{accelerator_count}')
            else:
                directive = f'#SBATCH --gres=gpu:{accelerator_count}'
        assert directive == '#SBATCH --gres=gpu:2'

    def test_no_gpu(self):
        """No GPU directive when count is 0."""
        accelerator_count = 0
        directive = ''
        if accelerator_count > 0:
            directive = f'#SBATCH --gres=gpu:{accelerator_count}'
        assert directive == ''


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


class TestSbatchOptionsPrecedence:
    """Test 4-level sbatch_options merge in make_deploy_resources_variables.

    Priority order (lowest to highest):
      1. ~/.sky/config.yaml  slurm.sbatch_options              (global)
      2. ~/.sky/config.yaml  slurm.cluster_configs.<c>.sbatch_options (cluster)
      3. ~/.sky/config.yaml  slurm.cluster_configs.<c>.partition_configs.<p>.sbatch_options (partition)
      4. Task YAML           config.slurm.sbatch_options        (task-level)

    Uses real config loading by writing to a tmp YAML file and reloading.
    """

    FAKE_SSH_CONFIG = {
        'hostname': '10.0.0.1',
        'port': '22',
        'user': 'slurm',
        'identityfile': ['/home/user/.ssh/id_rsa'],
    }

    def _load_config_and_get_sbatch_options(self,
                                            tmp_path,
                                            skypilot_config_dict,
                                            cluster_config_overrides=None):
        """Write config to tmp file, reload, and call make_deploy_resources_variables."""
        from sky import skypilot_config
        from sky.utils import yaml_utils

        # Write config dict to a tmp YAML file.
        config_path = tmp_path / 'config.yaml'
        config_path.write_text(yaml_utils.dump_yaml_str(skypilot_config_dict))

        # Point config loading at our tmp file and reload.
        saved_global = skypilot_config._GLOBAL_CONFIG_PATH
        saved_project = skypilot_config._PROJECT_CONFIG_PATH
        saved_ctx = skypilot_config._global_config_context
        try:
            skypilot_config._GLOBAL_CONFIG_PATH = str(config_path)
            skypilot_config._PROJECT_CONFIG_PATH = str(tmp_path /
                                                       'nonexistent.yaml')
            skypilot_config._global_config_context = (
                skypilot_config.ConfigContext())
            skypilot_config.reload_config()

            cloud = slurm_cloud.Slurm()

            mock_resources = mock.MagicMock(unsafe=True)
            mock_resources.zone = 'gpu'
            mock_resources.instance_type = '4CPU--16GB'
            mock_resources.assert_launchable.return_value = mock_resources
            mock_resources.extract_docker_image.return_value = None
            mock_resources.cluster_config_overrides = (cluster_config_overrides
                                                       or {})

            region = mock.MagicMock()
            region.name = 'mycluster'
            zone_mock = mock.MagicMock()
            zone_mock.name = 'gpu'

            mock_ssh_config = mock.MagicMock()
            mock_ssh_config.lookup.return_value = self.FAKE_SSH_CONFIG

            with patch(
                    'sky.clouds.slurm.slurm_utils.get_slurm_ssh_config',
                    return_value=mock_ssh_config), \
                 patch(
                    'sky.clouds.slurm.slurm_utils.get_partitions',
                    return_value=['gpu', 'cpu']), \
                 patch(
                    'sky.clouds.slurm.slurm_utils.resolve_gres_gpu_type',
                    side_effect=lambda cluster, t, count=1, partition=None: t):
                deploy_vars = cloud.make_deploy_resources_variables(
                    resources=mock_resources,
                    cluster_name=mock.MagicMock(),
                    region=region,
                    zones=[zone_mock],
                    num_nodes=1,
                )
            return deploy_vars['sbatch_options']
        finally:
            skypilot_config._GLOBAL_CONFIG_PATH = saved_global
            skypilot_config._PROJECT_CONFIG_PATH = saved_project
            skypilot_config._global_config_context = saved_ctx

    def test_global_only(self, tmp_path):
        """Level 1: Only global sbatch_options set."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path, {
                'slurm': {
                    'sbatch_options': {
                        'account': 'research',
                        'qos': 'normal',
                    },
                },
            })
        assert result == {'account': 'research', 'qos': 'normal'}

    def test_cluster_overrides_global(self, tmp_path):
        """Level 2 overrides level 1 for same key; new keys are merged."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path, {
                'slurm': {
                    'sbatch_options': {
                        'account': 'global-account',
                        'qos': 'normal',
                    },
                    'cluster_configs': {
                        'mycluster': {
                            'sbatch_options': {
                                'account': 'cluster-account',
                                'constraint': 'skylake',
                            },
                        },
                    },
                },
            })
        assert result == {
            'account': 'cluster-account',
            'qos': 'normal',
            'constraint': 'skylake',
        }

    def test_partition_overrides_cluster_and_global(self, tmp_path):
        """Level 3 overrides levels 1 and 2 for same key."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path, {
                'slurm': {
                    'sbatch_options': {
                        'account': 'global-account',
                        'qos': 'normal',
                    },
                    'cluster_configs': {
                        'mycluster': {
                            'sbatch_options': {
                                'account': 'cluster-account',
                            },
                            'partition_configs': {
                                'gpu': {
                                    'sbatch_options': {
                                        'account': 'partition-account',
                                        'exclusive': True,
                                    },
                                },
                            },
                        },
                    },
                },
            })
        assert result == {
            'account': 'partition-account',
            'qos': 'normal',
            'exclusive': True,
        }

    def test_task_overrides_all(self, tmp_path):
        """Level 4 (task YAML) overrides all config.yaml levels."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path,
            skypilot_config_dict={
                'slurm': {
                    'sbatch_options': {
                        'account': 'global-account',
                        'qos': 'normal',
                    },
                    'cluster_configs': {
                        'mycluster': {
                            'sbatch_options': {
                                'account': 'cluster-account',
                            },
                            'partition_configs': {
                                'gpu': {
                                    'sbatch_options': {
                                        'account': 'partition-account',
                                        'exclusive': True,
                                    },
                                },
                            },
                        },
                    },
                },
            },
            cluster_config_overrides={
                'slurm': {
                    'sbatch_options': {
                        'account': 'task-account',
                        'nice': 100,
                    },
                },
            },
        )
        assert result == {
            'account': 'task-account',
            'qos': 'normal',
            'exclusive': True,
            'nice': 100,
        }

    def test_task_only(self, tmp_path):
        """Level 4 alone, no config.yaml sbatch_options at all."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path,
            skypilot_config_dict={},
            cluster_config_overrides={
                'slurm': {
                    'sbatch_options': {
                        'account': 'task-only',
                    },
                },
            },
        )
        assert result == {'account': 'task-only'}

    def test_no_sbatch_options(self, tmp_path):
        """No sbatch_options at any level."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path, skypilot_config_dict={})
        assert result == {}

    def test_task_cluster_specific_override(self, tmp_path):
        """Task YAML with cluster-specific sbatch_options."""
        result = self._load_config_and_get_sbatch_options(
            tmp_path,
            skypilot_config_dict={
                'slurm': {
                    'sbatch_options': {
                        'account': 'global-account',
                    },
                },
            },
            cluster_config_overrides={
                'slurm': {
                    'cluster_configs': {
                        'mycluster': {
                            'sbatch_options': {
                                'account': 'task-cluster-account',
                            },
                        },
                    },
                },
            },
        )
        assert result == {'account': 'task-cluster-account'}


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
             patch('sky.clouds.slurm.slurm_utils.get_submit_user',
                   return_value=None), \
             patch('sky.clouds.slurm.slurm_utils.resolve_gres_gpu_type',
                   side_effect=lambda cluster, t, count=1, partition=None: t):
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
        mock_config.assert_any_call(cloud='slurm',
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
    @patch('sky.provision.slurm.instance.command_runner.'
           'SlurmLoginNodeCommandRunner')
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
                                                              60 * 60,
                                                              default_time=None)
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
    @patch('sky.provision.slurm.instance.command_runner.'
           'SlurmLoginNodeCommandRunner')
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
                                                              60 * 60,
                                                              default_time=None)
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
            name=partition_name,
            is_default=False,
            maxtime=7 * 24 * 60 * 60,
            default_time=None)

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

    @staticmethod
    def _make_non_container_config(cpus):
        from sky.provision import common

        return common.ProvisionConfig(
            provider_config={
                'ssh': {
                    'hostname': 'login.example.com',
                    'port': '22',
                    'user': 'root',
                    'private_key': '/path/to/key',
                },
                'cluster': 'test-slurm',
                'partition': 'cpus',
                'provision_timeout': 300,
                'slurm_user': 'alice',
            },
            authentication_config={},
            docker_config={},
            node_config={
                'cpus': cpus,
                'memory': 8,
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.'
           'SlurmLoginNodeCommandRunner')
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

        config.node_config['volume_mounts'] = [
            {
                'path': '/data',
                'volume_name': '',
                'volume_config': {
                    'config': {
                        'host_path': '/host/data',
                        'mode': 'ro',
                    },
                },
                'is_ephemeral': False,
            },
            {
                'path': '/scratch',
                'volume_name': '',
                'volume_config': {
                    'config': {
                        'host_path': '/nvme/$SLURM_JOB_ID',
                        'mode': 'rw',
                    },
                },
                'is_ephemeral': False,
            },
            {
                # No 'mode': must fail closed to read-only.
                'path': '/models',
                'volume_name': '',
                'volume_config': {
                    'config': {
                        'host_path': '/host/models',
                    },
                },
                'is_ephemeral': False,
            },
        ]
        mounted_script = self._run_and_capture_script('test-cluster-mounted',
                                                      config)
        assert ('--container-mounts="/home/testuser:/home/testuser,'
                '/tmp/ccache_$(id -u):/var/cache/ccache,'
                '/host/data:/data:ro,/nvme/$SLURM_JOB_ID:/scratch,'
                '/host/models:/models:ro"' in mounted_script)

    @pytest.mark.parametrize('path', [
        '/host/data,other',
        '/host/data:/other',
        '/host/data with-space',
        '/host/"data"',
        '/host/`id`',
        '/host/$(id)',
        '/host/${HOME}',
        '/host/data\nmalicious',
        'relative/path',
    ])
    def test_container_mount_rejects_unsafe_paths(self, path):
        with pytest.raises(ValueError, match='Invalid Pyxis container mount'):
            slurm_instance._validate_pyxis_mount_path(path, 'source')

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.'
           'SlurmLoginNodeCommandRunner')
    def test_non_container_script_format(self, mock_ssh_runner,
                                         mock_slurm_client,
                                         mock_get_partition_info,
                                         mock_get_proctrack_type,
                                         mock_wait_for_job_nodes):
        """Test that sbatch provision script without containers is correct."""
        self._setup_mocks(mock_ssh_runner, mock_slurm_client,
                          mock_get_partition_info, 'cpus')
        mock_get_proctrack_type.return_value = 'cgroup'

        config = self._make_non_container_config(cpus=2)
        written_script = self._run_and_capture_script(
            'test-cluster-no-container', config)

        assert_sbatch_matches_snapshot('basic', written_script)
        assert mock_slurm_client.call_args.kwargs['slurm_user'] == 'alice'
        assert mock_ssh_runner.call_args.kwargs['slurm_user'] == 'alice'

    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.'
           'SlurmLoginNodeCommandRunner')
    def test_fractional_cpus_are_rounded_up(self, mock_ssh_runner,
                                            mock_slurm_client,
                                            mock_get_partition_info,
                                            mock_get_proctrack_type,
                                            mock_wait_for_job_nodes):
        self._setup_mocks(mock_ssh_runner, mock_slurm_client,
                          mock_get_partition_info, 'cpus')
        mock_get_proctrack_type.return_value = 'cgroup'

        config = self._make_non_container_config(cpus=1.5)
        written_script = self._run_and_capture_script(
            'test-cluster-fractional-cpus', config)

        assert '#SBATCH --cpus-per-task=2' in written_script

    @pytest.mark.parametrize('memory_gb,expected_mem_mb', [
        (0.5, 512),
        (1.5, 1536),
        (8, 8192),
        (16, 16384),
        (0.25, 256),
    ])
    @patch('sky.provision.slurm.instance._wait_for_job_nodes')
    @patch('sky.provision.slurm.instance.slurm_utils.get_proctrack_type')
    @patch('sky.provision.slurm.instance.slurm_utils.get_partition_info')
    @patch('sky.provision.slurm.instance.slurm.SlurmClient')
    @patch('sky.provision.slurm.instance.command_runner.'
           'SlurmLoginNodeCommandRunner')
    def test_fractional_memory_converted_to_mb(self, mock_ssh_runner,
                                               mock_slurm_client,
                                               mock_get_partition_info,
                                               mock_get_proctrack_type,
                                               mock_wait_for_job_nodes,
                                               memory_gb, expected_mem_mb):
        """Test that fractional GB memory is correctly converted to MB."""
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
                'memory': memory_gb,
            },
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        )

        written_script = self._run_and_capture_script('test-cluster-frac-mem',
                                                      config)
        assert f'#SBATCH --mem={expected_mem_mb}M' in written_script


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


class TestHelperPathFunctions:
    """Test path helper functions in sky/provision/slurm/instance.py."""

    @pytest.mark.parametrize('base_dir,job_id,expected', [
        ('/home/user', '123', '/home/user/.sky_provision/slurm-123.out'),
        ('/fsx/ubuntu', '456', '/fsx/ubuntu/.sky_provision/slurm-456.out'),
        ('/home/user', '%j', '/home/user/.sky_provision/slurm-%j.out'),
    ])
    def test_sbatch_log_path(self, base_dir, job_id, expected):
        assert slurm_instance._sbatch_log_path(base_dir, job_id) == expected

    @pytest.mark.parametrize('base_dir,cluster,expected', [
        ('/home/user', 'my-cluster', '/home/user/.sky_clusters/my-cluster'),
        ('/fsx/ubuntu', 'test-abc123', '/fsx/ubuntu/.sky_clusters/test-abc123'),
    ])
    def test_sky_cluster_home_dir(self, base_dir, cluster, expected):
        assert slurm_instance._sky_cluster_home_dir(base_dir,
                                                    cluster) == expected

    @pytest.mark.parametrize('base_dir,cluster,expected', [
        ('/home/user', 'my-cluster', '/home/user/.sky_provision/my-cluster.sh'),
        ('~', 'my-cluster', '~/.sky_provision/my-cluster.sh'),
    ])
    def test_sbatch_provision_script_path(self, base_dir, cluster, expected):
        assert slurm_instance._sbatch_provision_script_path(base_dir,
                                                            cluster) == expected

    @pytest.mark.parametrize('tmpdir,cluster,expected', [
        (None, 'my-cluster', '/tmp/my-cluster'),
        ('/scratch/tmp', 'my-cluster', '/scratch/tmp/my-cluster'),
    ])
    def test_skypilot_runtime_dir(self, tmpdir, cluster, expected):
        assert slurm_instance._skypilot_runtime_dir(tmpdir, cluster) == expected


class TestGetEnv:
    """Test SlurmClient.get_env() parsing."""

    def test_parses_env_output(self):
        client = mock.MagicMock(spec=slurm.SlurmClient)
        client._run_slurm_cmd.return_value = (
            0, 'HOME=/home/ubuntu\nUSER=ubuntu\n', '')
        # Call the real method with the mocked client
        env = slurm.SlurmClient.get_env(client)
        assert env == {'HOME': '/home/ubuntu', 'USER': 'ubuntu'}

    def test_handles_values_with_equals(self):
        client = mock.MagicMock(spec=slurm.SlurmClient)
        client._run_slurm_cmd.return_value = (0,
                                              'PATH=/usr/bin:/bin\nFOO=a=b\n',
                                              '')
        env = slurm.SlurmClient.get_env(client)
        assert env['PATH'] == '/usr/bin:/bin'
        assert env['FOO'] == 'a=b'

    def test_command_failure_returns_empty(self):
        client = mock.MagicMock(spec=slurm.SlurmClient)
        client._run_slurm_cmd.return_value = (1, '', 'Connection refused')
        env = slurm.SlurmClient.get_env(client)
        assert env == {}


class TestExpandPathVars:
    """Test expand_path_vars with Python-side variable expansion."""

    REMOTE_ENV = {'USER': 'ubuntu', 'HOME': '/home/ubuntu'}

    def test_expands_dollar_var(self):
        result = slurm_utils.expand_path_vars('/fsx/$USER', self.REMOTE_ENV)
        assert result == '/fsx/ubuntu'

    def test_expands_braced_var(self):
        result = slurm_utils.expand_path_vars('/fsx/${USER}', self.REMOTE_ENV)
        assert result == '/fsx/ubuntu'

    def test_expands_multiple_vars(self):
        result = slurm_utils.expand_path_vars('$HOME/$USER', self.REMOTE_ENV)
        assert result == '/home/ubuntu/ubuntu'

    def test_unknown_var_left_unchanged(self):
        result = slurm_utils.expand_path_vars('/fsx/$UNKNOWN', self.REMOTE_ENV)
        assert result == '/fsx/$UNKNOWN'

    def test_no_vars_passthrough(self):
        result = slurm_utils.expand_path_vars('/fsx/ubuntu', self.REMOTE_ENV)
        assert result == '/fsx/ubuntu'

    def test_injection_not_expanded(self):
        # Even if someone puts shell metacharacters in config,
        # they are never sent to a shell — just literal string replacement.
        result = slurm_utils.expand_path_vars('/home/; rm -rf /',
                                              self.REMOTE_ENV)
        assert result == '/home/; rm -rf /'

"""Tests for UpCloud cloud provider."""
# pylint: disable=protected-access

import os
from types import SimpleNamespace
from typing import Any, Dict

import jinja2
import pytest
import yaml

import sky
from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky.adaptors import upcloud as upcloud_adaptor
from sky.clouds import upcloud
from sky.provision.upcloud import utils
from sky.utils import resources_utils

_ZONE = 'fi-hel2'
_L40S = 'GPU-8xCPU-64GB-1xL40S'
_CPU = 'CLOUDNATIVE-2xCPU-4GB'


@pytest.fixture
def no_token(monkeypatch, tmp_path):
    monkeypatch.setattr(upcloud_adaptor, 'UPCTL_CONFIG_PATH',
                        str(tmp_path / 'upctl.yaml'))
    return tmp_path / 'upctl.yaml'


def test_upcloud_cloud():
    cloud = upcloud.UpCloud()
    assert cloud.canonical_name() == 'upcloud'
    assert repr(cloud) == 'UpCloud'
    assert cloud.max_cluster_name_length() == 64
    assert sky.UpCloud is upcloud.UpCloud
    assert not hasattr(sky, 'Upcloud')
    assert clouds.UpCloud().is_same_cloud(sky.clouds.UpCloud())


def test_upcloud_unsupported_features():
    cloud = upcloud.UpCloud()
    for value in cloud._CLOUD_UNSUPPORTED_FEATURES.values():
        assert isinstance(value, str)
    enabled = {
        clouds.CloudImplementationFeatures.STOP,
        clouds.CloudImplementationFeatures.MULTI_NODE,
        clouds.CloudImplementationFeatures.IMAGE_ID,
        clouds.CloudImplementationFeatures.DOCKER_IMAGE,
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER,
        clouds.CloudImplementationFeatures.OPEN_PORTS,
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING,
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS,
        clouds.CloudImplementationFeatures.SPOT_INSTANCE,
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER,
        clouds.CloudImplementationFeatures.AUTOSTOP,
        clouds.CloudImplementationFeatures.AUTODOWN,
    }
    for feature in enabled:
        assert feature not in cloud._CLOUD_UNSUPPORTED_FEATURES
    on_demand = cloud._unsupported_features_for_resources(
        SimpleNamespace(use_spot=False))
    assert clouds.CloudImplementationFeatures.STOP not in on_demand
    spot = cloud._unsupported_features_for_resources(
        SimpleNamespace(use_spot=True))
    assert 'preempted' in spot[clouds.CloudImplementationFeatures.STOP]


def test_disk_tier_to_upcloud():
    tiers = resources_utils.DiskTier
    assert utils.disk_tier_to_upcloud(tiers.LOW) == 'hdd'
    assert utils.disk_tier_to_upcloud(tiers.MEDIUM) == 'standard'
    for tier in (tiers.HIGH, tiers.BEST):
        assert utils.disk_tier_to_upcloud(tier) == 'maxiops'
    assert utils.disk_tier_to_upcloud(None) == 'standard'


def test_validate_region_zone():
    cloud = upcloud.UpCloud()
    assert cloud.validate_region_zone(_ZONE, None) == (_ZONE, None)
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone(_ZONE, 'a')


def test_hourly_cost_spot_cheaper():
    cloud = upcloud.UpCloud()
    assert (cloud.instance_type_to_hourly_cost(_L40S, True, _ZONE) <
            cloud.instance_type_to_hourly_cost(_L40S, False, _ZONE))
    assert cloud.accelerators_to_hourly_cost({'L40S': 1}, False) == 0.0
    assert cloud.get_egress_cost(100) == 0.0


def test_feasible_resources_by_accelerator():
    cloud = upcloud.UpCloud()
    res = resources_lib.Resources(cloud=cloud, accelerators={'L40S': 1})
    feasible = cloud._get_feasible_launchable_resources(res)
    assert _L40S in [r.instance_type for r in feasible.resources_list]
    spot = cloud._get_feasible_launchable_resources(
        resources_lib.Resources(cloud=cloud,
                                accelerators={'L40S': 1},
                                use_spot=True))
    assert _L40S in [r.instance_type for r in spot.resources_list]
    missing = cloud._get_feasible_launchable_resources(
        resources_lib.Resources(cloud=cloud, accelerators={'T4': 1}))
    assert not missing.resources_list


def test_feasible_resources_by_instance_type():
    cloud = upcloud.UpCloud()
    feasible = cloud._get_feasible_launchable_resources(
        resources_lib.Resources(cloud=cloud, instance_type=_CPU))
    assert [r.instance_type for r in feasible.resources_list] == [_CPU]
    missing = cloud._get_feasible_launchable_resources(
        resources_lib.Resources(cloud=cloud,
                                instance_type='GPU-SPOT-8xCPU-64GB-1xL40S'))
    assert not missing.resources_list
    assert 'not available' in missing.hint


def _deploy_vars(monkeypatch,
                 config: Dict[str, Any] = None,
                 **resource_kwargs) -> Dict[str, Any]:
    config = config or {}
    monkeypatch.setattr(
        'sky.clouds.upcloud.skypilot_config.get_effective_region_config',
        lambda cloud, region, keys, default_value: config.get(
            keys[0], default_value))
    res = resources_lib.Resources(infra=f'upcloud/{_ZONE}', **resource_kwargs)
    return upcloud.UpCloud().make_deploy_resources_variables(
        res, resources_utils.ClusterName('c', 'c'), clouds.Region(_ZONE), None,
        1)


def test_deploy_vars_gpu(monkeypatch):
    variables = _deploy_vars(monkeypatch, instance_type=_L40S, use_spot=True)
    assert variables['instance_type'] == _L40S
    assert variables['use_spot'] is True
    assert variables['default_image'] == 'gpu'
    assert variables['image_id'] is None
    assert variables['install_docker'] is False
    assert variables['upcloud_network'] is None
    assert variables['upcloud_network_name'] is None
    assert variables['upcloud_file_volumes'] == []
    assert 'L40S' in variables['custom_resources']


def test_deploy_vars_cpu_docker(monkeypatch):
    variables = _deploy_vars(monkeypatch,
                             instance_type=_CPU,
                             image_id='docker:ubuntu:22.04',
                             disk_tier='low')
    assert variables['default_image'] == 'cpu'
    assert variables['install_docker'] is True
    assert variables['disk_tier'] == 'hdd'
    assert 'docker_run_options' not in variables


def test_deploy_vars_gpu_docker_requests_gpus(monkeypatch):
    variables = _deploy_vars(monkeypatch,
                             instance_type=_L40S,
                             image_id='docker:nvcr.io/nvidia/pytorch:24.10-py3')
    assert variables['docker_run_options'] == ['--gpus all']


def test_credential_file_mounts(monkeypatch):
    monkeypatch.setattr(upcloud_adaptor, 'credential_file_exists', lambda: True)
    monkeypatch.setattr(upcloud_adaptor,
                        'upcloud_profile_in_aws_cred_and_config', lambda: False)
    mounts = upcloud.UpCloud().get_credential_file_mounts()
    assert mounts == {'~/.config/upctl.yaml': '~/.config/upctl.yaml'}


def test_credential_file_mounts_with_object_storage(monkeypatch):
    monkeypatch.setattr(upcloud_adaptor, 'credential_file_exists',
                        lambda: False)
    monkeypatch.setattr(upcloud_adaptor,
                        'upcloud_profile_in_aws_cred_and_config', lambda: True)
    mounts = upcloud.UpCloud().get_credential_file_mounts()
    assert mounts == {
        '~/.aws/credentials': '~/.aws/credentials',
        '~/.aws/config': '~/.aws/config',
    }


def test_check_credentials_missing(no_token):
    del no_token
    valid, msg = upcloud.UpCloud._check_compute_credentials()
    assert not valid
    assert 'UPCLOUD_TOKEN' not in msg
    assert 'upctl.yaml' in msg


def test_check_credentials_present(monkeypatch):
    monkeypatch.setattr(upcloud_adaptor, 'check_compute_credentials',
                        lambda: True)
    assert upcloud.UpCloud._check_compute_credentials() == (True, None)


def test_create_image_requires_single_node(monkeypatch):
    monkeypatch.setattr('sky.clouds.upcloud.provision_lib.query_instances',
                        lambda *a, **k: {
                            'a': (None, None),
                            'b': (None, None)
                        })
    name = resources_utils.ClusterName(display_name='demo',
                                       name_on_cloud='demo')
    with pytest.raises(exceptions.NotSupportedError):
        upcloud.UpCloud.create_image_from_cluster(name, _ZONE, None)


def _render_template(**overrides) -> Dict[str, Any]:
    template_path = os.path.join(os.path.dirname(sky.__file__), 'templates',
                                 'upcloud-ray.yml.j2')
    with open(template_path, encoding='utf-8') as f:
        template = jinja2.Template(f.read())
    variables: Dict[str, Any] = {
        'cluster_name_on_cloud': 'demo',
        'num_nodes': 2,
        'docker_image': None,
        'region': _ZONE,
        'use_internal_ips': False,
        'upcloud_network': None,
        'upcloud_network_name': None,
        'ssh_private_key': '~/.ssh/sky-key',
        'ssh_proxy_command': None,
        'instance_type': _L40S,
        'use_spot': False,
        'disk_size': 256,
        'disk_tier': 'maxiops',
        'image_id': None,
        'default_image': 'gpu',
        'install_docker': False,
        'upcloud_file_volumes': [],
        'credentials': {},
        'sky_ray_yaml_remote_path': 'r',
        'sky_ray_yaml_local_path': 'l',
        'sky_remote_path': 'r',
        'sky_wheel_hash': 'h',
        'sky_local_path': 'l',
        'initial_setup_commands': [],
        'conda_installation_commands': '',
        'uv_installation_commands': '',
        'ray_skypilot_installation_commands': '',
        'copy_skypilot_templates_commands': '',
        'ssh_max_sessions_config': '',
    }
    variables.update(overrides)
    return yaml.safe_load(template.render(**variables))


def test_network_name_reaches_provider_config(monkeypatch):
    variables = _deploy_vars(monkeypatch,
                             config={'network_name': 'my-net'},
                             instance_type=_CPU)
    assert variables['upcloud_network_name'] == 'my-net'
    provider = _render_template(upcloud_network_name='my-net')['provider']
    assert provider['network_name'] == 'my-net'
    assert 'network' not in provider
    assert 'network_name' not in _render_template()['provider']


def test_template_file_volume_mounted_on_all_nodes():
    config = _render_template(upcloud_file_volumes=[{
        'address': '10.101.0.2',
        'share_path': '/skypilot',
        'path': '/data'
    }])
    setup = config['setup_commands'][0]
    assert 'nfs-common' in setup
    assert '10.101.0.2:/skypilot /data nfs' in setup
    assert '-head)' not in setup

"""Test the controller_utils module."""
from typing import Any, Dict

import pytest

import sky
from sky.jobs import constants as managed_job_constants
from sky.serve import constants as serve_constants
from sky.utils import controller_utils


@pytest.mark.parametrize(
    ('controller_type', 'custom_controller_resources_config', 'expected'), [
        ('jobs', {}, {
            'cpus': '8+',
            'memory': '3x',
            'disk_size': 50
        }),
        ('jobs', {
            'cpus': '4+',
            'disk_size': 100
        }, {
            'cpus': '4+',
            'memory': '3x',
            'disk_size': 100
        }),
        ('serve', {}, {
            'cpus': '4+',
            'disk_size': 200
        }),
        ('serve', {
            'memory': '32+',
        }, {
            'cpus': '4+',
            'memory': '32+',
            'disk_size': 200
        }),
    ])
def test_get_controller_resources(
    controller_type: str,
    custom_controller_resources_config: Dict[str, Any],
    expected: Dict[str, Any],
    enable_all_clouds,
    monkeypatch,
):

    def get_custom_controller_resources(keys, default):
        if keys == (controller_type, 'controller', 'resources'):
            return custom_controller_resources_config
        else:
            return default

    monkeypatch.setattr('sky.skypilot_config.loaded', lambda: True)
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        get_custom_controller_resources)

    controller_resources = list(
        controller_utils.get_controller_resources(
            controller=controller_utils.Controllers.from_type(controller_type),
            task_resources=[]))[0]
    controller_resources_config = controller_resources.to_yaml_config()
    for k, v in expected.items():
        assert controller_resources_config[k] == v, (
            controller_type, custom_controller_resources_config, expected,
            controller_resources_config, k, v)


@pytest.mark.parametrize(('controller_type', 'default_controller_resources'), [
    ('jobs', managed_job_constants.CONTROLLER_RESOURCES),
    ('serve', serve_constants.CONTROLLER_RESOURCES),
])
def test_get_controller_resources_with_task_resources(
    controller_type: str,
    default_controller_resources: Dict[str, Any],
    enable_all_clouds,
):

    # 1. All resources has cloud specified. All of them
    # could host controllers. Return a set, each item has
    # one cloud specified plus the default resources.
    all_clouds = {sky.AWS(), sky.GCP(), sky.Azure()}
    all_cloud_names = {str(c) for c in all_clouds}
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(cloud=c) for c in all_clouds])
    for r in controller_resources:
        config = r.to_yaml_config()
        cloud = config.pop('cloud')
        assert cloud in all_cloud_names
        all_cloud_names.remove(cloud)
        assert config == default_controller_resources, config
    assert not all_cloud_names

    # 2. All resources has cloud specified. Some of them
    # could NOT host controllers. Return a set, only
    # containing those could host controllers.
    all_clouds = {
        sky.AWS(),
        sky.GCP(),
        sky.Azure(),
        sky.Fluidstack(),
        sky.Kubernetes(),
        sky.Lambda(),
        sky.RunPod()
    }

    def _could_host_controllers(cloud: sky.clouds.Cloud) -> bool:
        try:
            cloud.check_features_are_supported(
                sky.Resources(),
                {sky.clouds.CloudImplementationFeatures.HOST_CONTROLLERS})
        except sky.exceptions.NotSupportedError:
            return False
        return True

    all_cloud_names_expected = {
        str(c) for c in all_clouds if _could_host_controllers(c)
    }
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(cloud=c) for c in all_clouds])
    for r in controller_resources:
        config = r.to_yaml_config()
        cloud = config.pop('cloud')
        assert cloud in all_cloud_names_expected
        all_cloud_names_expected.remove(cloud)
        assert config == default_controller_resources, config
    assert not all_cloud_names_expected

    # 3. Some resources does not have cloud specified.
    # Return the default resources.
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(accelerators='L4'),
            sky.Resources(cloud=sky.RunPod(), accelerators='A40'),
        ])
    assert len(controller_resources) == 1
    config = list(controller_resources)[0].to_yaml_config()
    assert config == default_controller_resources, config

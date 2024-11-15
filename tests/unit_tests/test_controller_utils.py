"""Test the controller_utils module."""
from typing import Any, Dict, Optional, Set, Tuple

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


def _check_controller_resources(
        controller_resources: Set[sky.Resources],
        expected_combinations: Set[Tuple[Optional[str], Optional[str],
                                         Optional[str]]],
        default_controller_resources: Dict[str, Any]) -> None:
    """Helper function to check that the controller resources match the
    expected combinations."""
    for r in controller_resources:
        config = r.to_yaml_config()
        cloud = config.pop('cloud')
        region = config.pop('region', None)
        zone = config.pop('zone', None)
        assert (cloud, region, zone) in expected_combinations
        expected_combinations.remove((cloud, region, zone))
        assert config == default_controller_resources, config
    assert not expected_combinations


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
    expected_combinations = {(str(c), None, None) for c in all_clouds}
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(cloud=c) for c in all_clouds])
    _check_controller_resources(controller_resources, expected_combinations,
                                default_controller_resources)

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

    expected_combinations = {
        (str(c), None, None) for c in all_clouds if _could_host_controllers(c)
    }
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(cloud=c) for c in all_clouds])
    _check_controller_resources(controller_resources, expected_combinations,
                                default_controller_resources)

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

    # 4. All resources have clouds, regions, and zones specified.
    # Return a set of controller resources for all combinations of clouds,
    # regions, and zones. Each combination should contain the default resources
    # along with the cloud, region, and zone.
    all_cloud_regions_zones = [
        sky.Resources(cloud=sky.AWS(), region='us-east-1', zone='us-east-1a'),
        sky.Resources(cloud=sky.AWS(), region='ap-south-1', zone='ap-south-1b'),
        sky.Resources(cloud=sky.GCP(),
                      region='us-central1',
                      zone='us-central1-a'),
        sky.Resources(cloud=sky.GCP(),
                      region='europe-west1',
                      zone='europe-west1-b')
    ]
    expected_combinations = {('AWS', 'us-east-1', 'us-east-1a'),
                             ('AWS', 'ap-south-1', 'ap-south-1b'),
                             ('GCP', 'us-central1', 'us-central1-a'),
                             ('GCP', 'europe-west1', 'europe-west1-b')}
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=all_cloud_regions_zones)
    _check_controller_resources(controller_resources, expected_combinations,
                                default_controller_resources)

    # 5. Clouds and regions are specified, but zones are partially specified.
    # Return a set containing combinations where the zone is None when not all
    # zones are specified in the input for the given region. The default
    # resources should be returned along with the cloud and region, and the
    # zone (if specified).
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(cloud=sky.AWS(), region='us-west-2'),
            sky.Resources(cloud=sky.AWS(),
                          region='us-west-2',
                          zone='us-west-2b'),
            sky.Resources(cloud=sky.GCP(),
                          region='us-central1',
                          zone='us-central1-a')
        ])
    expected_combinations = {('AWS', 'us-west-2', None),
                             ('GCP', 'us-central1', 'us-central1-a')}
    _check_controller_resources(controller_resources, expected_combinations,
                                default_controller_resources)

    # 6. Mixed case: Some resources have clouds and regions or zones, others do
    # not. For clouds where regions or zones are not specified in the input,
    # return None for those fields. The default resources should be returned
    # along with the cloud, region (if specified), and zone (if specified).
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(cloud=sky.GCP(), region='europe-west1'),
            sky.Resources(cloud=sky.GCP()),
            sky.Resources(cloud=sky.AWS(),
                          region='eu-north-1',
                          zone='eu-north-1a'),
            sky.Resources(cloud=sky.AWS(), region='eu-north-1'),
            sky.Resources(cloud=sky.AWS(), region='ap-south-1'),
            sky.Resources(cloud=sky.Azure()),
        ])
    expected_combinations = {
        ('AWS', 'eu-north-1', None),
        ('AWS', 'ap-south-1', None),
        ('GCP', None, None),
        ('Azure', None, None),
    }
    _check_controller_resources(controller_resources, expected_combinations,
                                default_controller_resources)

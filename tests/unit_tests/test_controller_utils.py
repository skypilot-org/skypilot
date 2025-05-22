"""Test the controller_utils module."""
from typing import Any, Dict, Optional, Set, Tuple

import pytest

import sky
from sky.jobs import constants as managed_job_constants
from sky.serve import constants as serve_constants
from sky.utils import controller_utils
from sky.utils import registry

_DEFAULT_AUTOSTOP = {
    'down': False,
    'idle_minutes': 10,
}


@pytest.mark.parametrize(
    ('controller_type', 'custom_controller_resources_config', 'expected'), [
        ('jobs', {}, {
            'cpus': '4+',
            'memory': '8x',
            'disk_size': 50,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
        ('jobs', {
            'cpus': '4+',
            'disk_size': 100,
        }, {
            'cpus': '4+',
            'memory': '8x',
            'disk_size': 100,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
        ('serve', {}, {
            'cpus': '4+',
            'disk_size': 200,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
        ('serve', {
            'memory': '32+',
        }, {
            'cpus': '4+',
            'memory': '32+',
            'disk_size': 200,
            'autostop': _DEFAULT_AUTOSTOP,
        }),
    ])
def test_get_controller_resources(controller_type: str,
                                  custom_controller_resources_config: Dict[str,
                                                                           Any],
                                  expected: Dict[str, Any], monkeypatch,
                                  enable_all_clouds):

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
        assert controller_resources_config.get(k) == v, (
            controller_type, custom_controller_resources_config, expected,
            controller_resources_config, k, v)


def _check_controller_resources(
        controller_resources: Set[sky.Resources], expected_infra_list: Set[str],
        default_controller_resources: Dict[str, Any]) -> None:
    """Helper function to check that the controller resources match the
    expected combinations."""
    for r in controller_resources:
        config = r.to_yaml_config()
        infra = config.pop('infra')
        assert infra in expected_infra_list
        expected_infra_list.remove(infra)
        assert config == default_controller_resources, config
    assert not expected_infra_list


@pytest.mark.parametrize(('controller_type', 'default_controller_resources'), [
    ('jobs', {
        **managed_job_constants.CONTROLLER_RESOURCES,
        'autostop': _DEFAULT_AUTOSTOP,
    }),
    ('serve', {
        **serve_constants.CONTROLLER_RESOURCES,
        'autostop': _DEFAULT_AUTOSTOP,
    }),
])
def test_get_controller_resources_with_task_resources(
        controller_type: str, default_controller_resources: Dict[str, Any],
        enable_all_clouds):

    # 1. All resources has cloud specified. All of them
    # could host controllers. Return a set, each item has
    # one cloud specified plus the default resources.
    all_clouds = {'aws', 'gcp', 'azure'}
    expected_infra_set = all_clouds
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(infra=c) for c in all_clouds])
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 2. All resources has cloud specified. Some of them
    # could NOT host controllers. Return a set, only
    # containing those could host controllers.
    all_clouds = {
        'aws', 'gcp', 'azure', 'fluidstack', 'kubernetes', 'lambda', 'runpod'
    }

    def _could_host_controllers(cloud_str: str) -> bool:
        cloud = registry.CLOUD_REGISTRY.from_str(cloud_str)
        try:
            cloud.check_features_are_supported(
                sky.Resources(),
                {sky.clouds.CloudImplementationFeatures.HOST_CONTROLLERS})
        except sky.exceptions.NotSupportedError:
            return False
        return True

    expected_infra_set = {c for c in all_clouds if _could_host_controllers(c)}
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[sky.Resources(infra=c) for c in all_clouds])
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 3. Some resources does not have cloud specified.
    # Return the default resources.
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(accelerators='L4'),
            sky.Resources(infra='runpod', accelerators='A40'),
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
                      zone='europe-west1-b'),
    ]
    expected_infra_set = {
        'aws/us-east-1/us-east-1a',
        'aws/ap-south-1/ap-south-1b',
        'gcp/us-central1/us-central1-a',
        'gcp/europe-west1/europe-west1-b',
    }
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=all_cloud_regions_zones)
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

    # 5. Clouds and regions are specified, but zones are partially specified.
    # Return a set containing combinations where the zone is None when not all
    # zones are specified in the input for the given region. The default
    # resources should be returned along with the cloud and region, and the
    # zone (if specified).
    controller_resources = controller_utils.get_controller_resources(
        controller=controller_utils.Controllers.from_type(controller_type),
        task_resources=[
            sky.Resources(infra='aws/us-west-2'),
            sky.Resources(infra='aws/us-west-2/us-west-2b'),
            sky.Resources(infra='gcp/us-central1/us-central1-a')
        ])
    expected_infra_set = {
        'aws/us-west-2',
        'gcp/us-central1/us-central1-a',
    }
    _check_controller_resources(controller_resources, expected_infra_set,
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
    expected_infra_set = {
        'aws/eu-north-1',
        'aws/ap-south-1',
        'gcp',
        'azure',
    }
    _check_controller_resources(controller_resources, expected_infra_set,
                                default_controller_resources)

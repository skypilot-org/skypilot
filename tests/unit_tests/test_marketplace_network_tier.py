"""Tests for marketplace provider network-tier support."""
# pylint: disable=protected-access

from unittest import mock

import pytest

from sky import clouds
from sky import resources as resources_lib
from sky.clouds import runpod as runpod_cloud
from sky.clouds import vast as vast_cloud
from sky.utils import resources_utils


@pytest.mark.parametrize('cloud_cls', [runpod_cloud.RunPod, vast_cloud.Vast])
def test_best_network_tier_is_supported(cloud_cls):
    resources = resources_lib.Resources(cloud=cloud_cls(),
                                        accelerators={'A100': 1},
                                        network_tier='best')

    unsupported = cloud_cls._unsupported_features_for_resources(resources)

    assert (clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER
            not in unsupported)


def test_runpod_deploy_variables_include_network_tier():
    cloud = runpod_cloud.RunPod()
    resources = resources_lib.Resources(
        cloud=cloud,
        instance_type='1x_A100-80GB_SECURE',
        network_tier='best',
    )
    region = clouds.Region('US').set_zones([clouds.Zone('US-CA-2')])

    with mock.patch.object(cloud,
                           'get_accelerators_from_instance_type',
                           return_value={'A100-80GB': 1}), mock.patch.object(
                               cloud,
                               'instance_type_to_hourly_cost',
                               return_value=1.0):
        deploy_vars = cloud.make_deploy_resources_variables(
            resources=resources,
            cluster_name=resources_utils.ClusterName('test', 'test'),
            region=region,
            zones=region.zones,
            num_nodes=1,
        )

    assert deploy_vars['network_tier'] == 'best'


def test_vast_deploy_variables_include_network_tier():
    cloud = vast_cloud.Vast()
    resources = resources_lib.Resources(
        cloud=cloud,
        instance_type='1x-A100-4-8192',
        network_tier='best',
    )

    with mock.patch.object(
            cloud, 'get_accelerators_from_instance_type',
            return_value={'A100': 1}), mock.patch(
                'sky.clouds.vast.skypilot_config.get_effective_region_config',
                side_effect=lambda **kwargs: kwargs['default_value']):
        deploy_vars = cloud.make_deploy_resources_variables(
            resources=resources,
            cluster_name=resources_utils.ClusterName('test', 'test'),
            region=clouds.Region('US'),
            zones=None,
            num_nodes=1,
        )

    assert deploy_vars['network_tier'] == 'best'

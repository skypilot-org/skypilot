"""Regression tests for Vast marketplace network-tier support."""
# pylint: disable=protected-access

from unittest import mock

from sky import clouds
from sky import resources as resources_lib
from sky.clouds import vast as vast_cloud
from sky.utils import resources_utils


def test_vast_best_network_tier_is_supported():
    """Ensure Vast accepts the public-bandwidth ``best`` network tier."""
    resources = resources_lib.Resources(cloud=vast_cloud.Vast(),
                                        accelerators={'A100': 1},
                                        network_tier='best')

    unsupported = vast_cloud.Vast._unsupported_features_for_resources(resources)

    assert (clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER
            not in unsupported)


def test_vast_deploy_variables_include_network_tier():
    """Ensure the selected Vast network tier reaches the cluster template."""
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

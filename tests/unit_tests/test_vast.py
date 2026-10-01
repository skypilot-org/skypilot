"""Tests for Vast cloud provider."""

from unittest import mock

from sky import clouds
from sky.clouds import vast
from sky.utils import resources_utils


def test_deploy_variables_pass_use_spot():
    resources = mock.MagicMock(unsafe=True,
                               instance_type='1x-RTX_3090-8-32768',
                               image_id=None,
                               use_spot=True,
                               cluster_config_overrides={})
    resources.assert_launchable.return_value = resources
    with mock.patch.object(vast.Vast,
                           'get_accelerators_from_instance_type',
                           return_value={'RTX3090': 1}):
        deploy_vars = vast.Vast().make_deploy_resources_variables(
            resources, resources_utils.ClusterName('c', 'c'),
            clouds.Region('US'), None, 1)
    assert deploy_vars['use_spot'] is True

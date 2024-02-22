"""Test the controller_utils module."""
import pytest

from sky.utils import controller_utils

@pytest.mark.parametrize(('controller_type', 'controller_resources_config'), [
    ('spot', {}, {'cpus': '8', 'memory': '32', 'disk_size': 50}),
    ('spot', {'cpus': '8+', 'memory': '3x', 'disk_size': 100}, {'cpus': '8', 'memory': '32', 'disk_size': 100}),
    ('serve', {}, {'cpus': '4', 'memory': '16', 'disk_size': 50}),
])
def test_get_controller_resources_spot(controller_type, controller_resources_config, expected, enable_all_clouds):
    controller_resources = controller_utils.get_controller_resources(controller_type, controller_resources_config)
    controller_resources_config = controller_resources.to_yaml_config()
    for k, v in expected.items():
        assert controller_resources[k] == v, (k, v, controller_resources_config)





"""Test the controller_utils module."""
import pytest

from sky.serve import constants as serve_constants
from sky.spot import constants as spot_constants
from sky.utils import controller_utils


@pytest.mark.parametrize(
    ('controller_type', 'custom_controller_resources_config', 'expected'), [
        ('spot', {}, {
            'cpus': '8+',
            'memory': '3x',
            'disk_size': 50
        }),
        ('spot', {
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
def test_get_controller_resources_spot(controller_type,
                                       custom_controller_resources_config,
                                       expected, enable_all_clouds,
                                       monkeypatch):
    if controller_type == 'spot':
        controller_resources_config = spot_constants.CONTROLLER_RESOURCES
    else:
        controller_resources_config = serve_constants.CONTROLLER_RESOURCES

    def get_custom_controller_resources(keys, default):
        if keys == (controller_type, 'controller', 'resources'):
            return custom_controller_resources_config
        else:
            return default

    monkeypatch.setattr('sky.skypilot_config.loaded', lambda: True)
    monkeypatch.setattr('sky.skypilot_config.get_nested',
                        get_custom_controller_resources)

    controller_resources = controller_utils.get_controller_resources(
        controller_type, controller_resources_config)
    controller_resources_config = controller_resources.to_yaml_config()
    for k, v in expected.items():
        assert controller_resources_config[k] == v, (
            controller_type, custom_controller_resources_config, expected,
            controller_resources_config, k, v)

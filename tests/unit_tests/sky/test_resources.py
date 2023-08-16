from unittest.mock import Mock

from sky.resources import Resources


def test_get_reservations_available_resources():
    mock = Mock()
    r = Resources(cloud=mock, instance_type="instance_type")
    r._region = "region"
    r._zone = "zone"
    r.get_reservations_available_resources(set())
    mock.get_reservations_available_resources.assert_called_once_with(
        "instance_type", "region", "zone", set())

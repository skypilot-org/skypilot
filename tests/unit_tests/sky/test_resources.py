from sky.resources import Resources
from unittest.mock import Mock


def test_get_available_reservation_resources():
    mock = Mock()
    r = Resources(cloud=mock, instance_type="instance_type")
    r._region = "region"
    r._zone = "zone"
    r.get_available_reservation_resources(set())
    mock.get_available_reservation_resources.assert_called_once_with(
        "instance_type", "region", "zone", set())


def test_filter_reservations_with_available_resources():
    mock = Mock()
    r = Resources(cloud=mock, instance_type="instance_type")
    r._region = "region"
    r._zone = "zone"
    r.filter_reservations_with_available_resources(set())
    mock.filter_reservations_with_available_resources.assert_called_once_with(
        "instance_type", "region", "zone", set())

from sky.resources import Resources
from unittest.mock import Mock


def test_get_available_reservation_resources():
    mock = Mock()
    r = Resources(cloud=mock, instance_type="instance_type")
    r._region = "region"
    r._zone = "zone"
    r._specific_reservations = []
    r.get_available_reservation_resources()
    mock.get_available_reservation_resources.assert_called_once_with(
        "instance_type", "region", "zone", [])

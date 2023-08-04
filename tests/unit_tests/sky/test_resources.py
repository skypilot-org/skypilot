from sky.resources import Resources
from unittest.mock import Mock


def test_get_available_reservation_resources():
    mock = Mock()
    Resources(cloud=mock).get_available_reservation_resources()
    mock.get_available_reservation_resources.assert_called_once()

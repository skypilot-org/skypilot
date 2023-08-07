from unittest.mock import patch
from sky.clouds.gcp import GCP, reservation_self_link_to_name
import pytest


@pytest.mark.parametrize((
    "mock_return", "expected_count"
), [([{
    "selfLink": "https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>",
    "specificReservation": {
        "count": "1",
        "inUseCount": "0",
    },
    "specificReservationRequired": True,
}], 1),
    ([{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "2",
            "inUseCount": "1",
        },
        "specificReservationRequired": False,
    }], 1),
    ([{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "1",
            "inUseCount": "0",
        },
        "specificReservationRequired": True,
    }], 0)])
def test_gcp_get_get_available_reservation_resources(mock_return,
                                                     expected_count):
    gcp = GCP()
    with patch.object(gcp,
                      '_list_reservations_for_instance_type_in_zone',
                      return_value=mock_return):
        count = gcp.get_available_reservation_resources(
            'instance_type', 'region', 'zone',
            {'projects/<project>/reservations/<reservation>'})
        assert count == expected_count


def test_reservation_self_link_to_name():
    assert reservation_self_link_to_name(
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation-name>'
    ) == 'projects/<project>/reservations/<reservation-name>'


@pytest.mark.parametrize((
    "specific_reservations", "mock_return", "expected_names"
), [({'projects/<project>/reservations/<reservation>'},[{
    "selfLink": "https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>",
    "specificReservation": {
        "count": "1",
        "inUseCount": "0",
    },
    "specificReservationRequired": True,
}], ['projects/<project>/reservations/<reservation>']),
    ({'projects/<project>/reservations/<reservation>'},[{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "2",
            "inUseCount": "1",
        },
        "specificReservationRequired": False,
    }], ['projects/<project>/reservations/<reservation>']),
    ({'projects/<project>/reservations/<reservation>'},[{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "1",
            "inUseCount": "0",
        },
        "specificReservationRequired": True,
    }], []),
        ({'projects/<project>/reservations/<reservation>'},[{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "1",
            "inUseCount": "0",
        },
        "specificReservationRequired": True,
    }], []),
            ({'projects/<project>/reservations/<reservation>'},[{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "1",
            "inUseCount": "0",
        },
        "specificReservationRequired": False,
    }], []),
        ({'projects/<project>/reservations/<reservation>'},[{
        "selfLink": "https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>",
        "specificReservation": {
            "count": "1",
            "inUseCount": "1",
        },
        "specificReservationRequired": True,
    }], []),
    ])
def test_filter_reservations_with_available_resources(specific_reservations, mock_return,
                                                     expected_names):
    gcp = GCP()
    with patch.object(gcp,
                '_list_reservations_for_instance_type_in_zone',
                return_value=mock_return):
        reservation_names = gcp.filter_reservations_with_available_resources(
        "instance_type", "region", "zone", specific_reservations)
        assert reservation_names == expected_names

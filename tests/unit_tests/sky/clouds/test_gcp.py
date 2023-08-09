from unittest.mock import patch
from sky.clouds.gcp import GCP, GCPReservation, SpecificReservation
import pytest


@pytest.mark.parametrize((
    'mock_return', 'expected_count'
), [([
    GCPReservation(
        self_link=
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
        specific_reservation=SpecificReservation(count=1, in_use_count=0),
        specific_reservation_required=True,
        zone='zone')
], 1),
    ([
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=2, in_use_count=1),
            specific_reservation_required=False,
            zone='zone')
    ], 1),
    ([
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=1, in_use_count=0),
            specific_reservation_required=True,
            zone='zone')
    ], 0)])
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


@pytest.mark.parametrize((
    'specific_reservations', 'mock_return', 'expected_names'
), [
    ({'projects/<project>/reservations/<reservation>'}, [
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=1, in_use_count=0),
            specific_reservation_required=True,
            zone='zone')
    ], ['projects/<project>/reservations/<reservation>']),
    ({'projects/<project>/reservations/<reservation>'}, [
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=2, in_use_count=1),
            specific_reservation_required=False,
            zone='zone')
    ], ['projects/<project>/reservations/<reservation>']),
    ({'projects/<project>/reservations/<reservation>'}, [
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=1, in_use_count=0),
            specific_reservation_required=True,
            zone='zone')
    ], []),
    ({'projects/<project>/reservations/<reservation>'}, [
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=1, in_use_count=0),
            specific_reservation_required=True,
            zone='zone')
    ], []),
    ({'projects/<project>/reservations/<reservation>'}, [
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=1, in_use_count=0),
            specific_reservation_required=False,
            zone='zone')
    ], []),
    ({'projects/<project>/reservations/<reservation>'}, [
        GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
            specific_reservation=SpecificReservation(count=1, in_use_count=1),
            specific_reservation_required=True,
            zone='zone')
    ], []),
])
def test_filter_reservations_with_available_resources(specific_reservations,
                                                      mock_return,
                                                      expected_names):
    gcp = GCP()
    with patch.object(gcp,
                      '_list_reservations_for_instance_type_in_zone',
                      return_value=mock_return):
        reservation_names = gcp.filter_reservations_with_available_resources(
            'instance_type', 'region', 'zone', specific_reservations)
        assert reservation_names == expected_names


def test_gcp_reservation_from_dict():
    r = GCPReservation.from_dict({
        'selfLink': 'test',
        'specificReservation': {
            'count': '1',
            'inUseCount': '0'
        },
        'specificReservationRequired': True,
        'zone': 'zone'
    })

    assert r.self_link == 'test'
    assert r.specific_reservation.count == 1
    assert r.specific_reservation.in_use_count == 0
    assert r.specific_reservation_required == True
    assert r.zone == 'zone'


@pytest.mark.parametrize(('count', 'in_use_count', 'expected'), [(1, 0, 1),
                                                                 (1, 1, 0)])
def test_gcp_reservation_available_resources(count, in_use_count, expected):
    r = GCPReservation(self_link='test',
                       specific_reservation=SpecificReservation(
                           count=count, in_use_count=in_use_count),
                       specific_reservation_required=True,
                       zone='zone')

    assert r.available_resources == expected


def test_gcp_reservation_name():
    r = GCPReservation(
        self_link=
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation-name>',
        specific_reservation=SpecificReservation(count=1, in_use_count=1),
        specific_reservation_required=True,
        zone='zone')
    assert r.name == 'projects/<project>/reservations/<reservation-name>'


@pytest.mark.parametrize(
    ('specific_reservations', 'specific_reservation_required', 'expected'), [
        ([], False, True),
        ([], True, False),
        (['projects/<project>/reservations/<reservation>'], True, True),
        (['projects/<project>/reservations/<invalid>'], True, False),
    ])
def test_gcp_reservation_is_consumable(specific_reservations,
                                       specific_reservation_required, expected):
    r = GCPReservation(
        self_link=
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
        specific_reservation=SpecificReservation(count=1, in_use_count=1),
        specific_reservation_required=specific_reservation_required,
        zone='zone')
    assert r.is_consumable(
        specific_reservations=specific_reservations) is expected

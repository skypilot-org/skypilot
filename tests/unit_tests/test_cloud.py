import pytest

from sky.clouds.cloud import Cloud


@pytest.mark.parametrize(("specific_reservations", "expected"), [({"a"}, {
    "a": 0
}), ((set(), {}))])
def test_cloud_get_reservations_available_resources(specific_reservations,
                                                    expected):

    available_resources = Cloud().get_reservations_available_resources(
        "instance_type", "region", "zone", specific_reservations)
    assert available_resources == expected

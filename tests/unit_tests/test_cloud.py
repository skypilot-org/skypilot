import pytest

from sky import exceptions
from sky.clouds.cloud import Cloud


@pytest.mark.parametrize(("specific_reservations", "expected"), [({"a"}, {
    "a": 0
}), ((set(), {}))])
def test_cloud_get_reservations_available_resources(specific_reservations,
                                                    expected):

    available_resources = Cloud().get_reservations_available_resources(
        "instance_type", "region", "zone", specific_reservations)
    assert available_resources == expected


class TestCheckClusterNameIsValid:

    def test_check(self):
        Cloud.check_cluster_name_is_valid("lora")

    def test_check_with_hyphen(self):
        Cloud.check_cluster_name_is_valid("seed-1")

    def test_check_with_characters_to_transform(self):
        Cloud.check_cluster_name_is_valid("Cuda_11.8")

    def test_check_when_starts_with_number(self):
        with pytest.raises(exceptions.InvalidClusterNameError):
            Cloud.check_cluster_name_is_valid("11.8cuda")

    def test_check_with_invalid_characters(self):
        with pytest.raises(exceptions.InvalidClusterNameError):
            Cloud.check_cluster_name_is_valid("lor@")

from sky.clouds.cloud import Cloud


def test_cloud_get_available_reservation_resources():

    available_resources = Cloud().get_available_reservation_resources(
        "instance_type", "region", "zone", set())
    assert available_resources == 0

def test_filter_reservations_with_available_resources():

    reservation_names = Cloud().filter_reservations_with_available_resources(
        "instance_type", "region", "zone", set())
    assert reservation_names == []

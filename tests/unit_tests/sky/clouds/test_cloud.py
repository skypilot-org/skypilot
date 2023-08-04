from sky.clouds.cloud import Cloud


def test_cloud_get_available_reservation_resources():

    available_resources = Cloud().get_available_reservation_resources(
        "instance_type", "region", "zone", [])
    assert available_resources == 0

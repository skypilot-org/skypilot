from sky.backends import wheel_utils


def test_build_wheels():
    assert wheel_utils.build_sky_wheel().exists()

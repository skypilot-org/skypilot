import time
import shutil
from sky.backends import wheel_utils


def test_build_wheels():
    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)
    start = time.time()
    assert wheel_utils.build_sky_wheel().exists()
    duration = time.time() - start

    start = time.time()
    assert wheel_utils.build_sky_wheel().exists()
    duration_cached = time.time() - start

    assert duration_cached < duration

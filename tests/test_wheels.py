import os
import shutil
import time

from sky.backends import wheel_utils


def test_build_wheels():
    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)
    start = time.time()
    wheel_path, _ = wheel_utils.build_sky_wheel()
    assert wheel_path.exists()
    duration = time.time() - start

    start = time.time()
    wheel_path, _ = wheel_utils.build_sky_wheel()
    assert wheel_path.exists()
    duration_cached = time.time() - start

    assert duration_cached < duration

    # simulate uncleaned symlinks due to interruption
    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)
    wheel_utils.WHEEL_DIR.mkdir()
    (wheel_utils.WHEEL_DIR / 'sky').symlink_to(wheel_utils.SKY_PACKAGE_PATH,
                                               target_is_directory=True)
    for root, _, _ in os.walk(str(wheel_utils.WHEEL_DIR)):
        # set file date to 1970-01-01 00:00 UTC
        os.utime(root, (0, 0))
    wheel_path, _ = wheel_utils.build_sky_wheel()
    assert wheel_path.exists()

    shutil.rmtree(wheel_utils.WHEEL_DIR, ignore_errors=True)

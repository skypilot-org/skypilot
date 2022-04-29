"""Utils for building sky pip wheels."""
from typing import Optional

import os
import pathlib
import shutil
import subprocess
import tempfile

import filelock

import sky
from sky.backends import backend_utils

# Local wheel path is same as the remote path.
WHEEL_DIR = pathlib.Path(os.path.expanduser(backend_utils.SKY_REMOTE_PATH))
SKY_PACKAGE_PATH = pathlib.Path(sky.__file__).parent.parent / 'sky'


def cleanup_wheels_dir(wheel_dir: pathlib.Path,
                       latest_wheel: Optional[pathlib.Path] = None) -> None:
    if latest_wheel is None:
        # Remove the entire dir.
        shutil.rmtree(wheel_dir, ignore_errors=True)
        return
    # Cleanup older wheels.
    for f in wheel_dir.iterdir():
        if f != latest_wheel:
            if f.is_dir() and not f.is_symlink():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()


def _get_latest_built_wheel() -> pathlib.Path:
    try:
        latest_wheel = max(WHEEL_DIR.glob('sky-*.whl'), key=os.path.getctime)
    except ValueError:
        raise FileNotFoundError('Could not find built Sky wheels.') from None
    return latest_wheel


def _build_sky_wheel() -> pathlib.Path:
    """Build a wheel for Sky."""
    # prepare files
    (WHEEL_DIR / 'sky').symlink_to(SKY_PACKAGE_PATH, target_is_directory=True)
    setup_files_dir = SKY_PACKAGE_PATH / 'setup_files'
    for f in setup_files_dir.iterdir():
        if f.is_file():
            shutil.copy(str(f), str(WHEEL_DIR))

    # It is important to normalize the path, otherwise 'pip wheel' would
    # treat the directory as a file and generate an empty wheel.
    norm_path = str(WHEEL_DIR) + os.sep
    try:
        # TODO(suquark): For python>=3.7, 'subprocess.run' supports capture
        # of the output.
        subprocess.run([
            'pip3', 'wheel', '--no-deps', norm_path, '--wheel-dir',
            str(WHEEL_DIR)
        ],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE,
                       check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError('Fail to build pip wheel for Sky. '
                           f'Error message: {e.stderr.decode()}') from e

    latest_wheel = _get_latest_built_wheel()
    cleanup_wheels_dir(WHEEL_DIR, latest_wheel)
    return WHEEL_DIR.absolute()


def build_sky_wheel() -> pathlib.Path:
    """Build a wheel for Sky, or reuse a cached wheel.

    Caller is responsible for removing the wheel.

    Returns:
        A newly made temporary path.
    """

    def _get_latest_modification_time(path: pathlib.Path) -> float:
        if not path.exists():
            return -1.
        try:
            return max(os.path.getmtime(root) for root, _, _ in os.walk(path))
        except ValueError:
            return -1.

    # This lock prevents that the wheel is updated while being copied.
    # Although the current caller already uses a lock, we still lock it here
    # to guarantee inherent consistency.
    with filelock.FileLock(WHEEL_DIR.parent / '.wheels_lock'):  # pylint: disable=E0110
        # This implements a classic "compare, update and clone" consistency
        # protocol. "compare, update and clone" has to be atomic to avoid
        # race conditions.
        last_modification_time = _get_latest_modification_time(SKY_PACKAGE_PATH)
        last_wheel_modification_time = _get_latest_modification_time(WHEEL_DIR)

        # only build wheels if the wheel is outdated
        if last_wheel_modification_time < last_modification_time:
            if not WHEEL_DIR.exists():
                WHEEL_DIR.mkdir(parents=True, exist_ok=True)
            _build_sky_wheel()

        # Use a newly made, unique temporary dir because there may be many
        # concurrent 'sky launch' happening.
        temp_wheel_dir = pathlib.Path(tempfile.mkdtemp())
        shutil.copy(_get_latest_built_wheel(), temp_wheel_dir)

    return temp_wheel_dir.absolute()

"""Utils for building sky pip wheels."""
import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

import filelock
from packaging import version

import sky
from sky.backends import backend_utils

# Local wheel path is same as the remote path.
WHEEL_DIR = pathlib.Path(os.path.expanduser(backend_utils.SKY_REMOTE_PATH))
SKY_PACKAGE_PATH = pathlib.Path(sky.__file__).parent.parent / 'sky'

# NOTE: keep the same as setup.py's setuptools.setup(name=..., ...).
_PACKAGE_WHEEL_NAME = 'skypilot'


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
    wheel_name = (f'**/{_PACKAGE_WHEEL_NAME}-'
                    f'{version.parse(sky.__version__)}-*.whl')
    try:
        latest_wheel_path = max(WHEEL_DIR.glob(wheel_name), key=os.path.getctime)
    except ValueError:
        raise FileNotFoundError(
            f'Could not find built SkyPilot wheels '
            f'under {WHEEL_DIR!r}') from None
    return latest_wheel_path


def _build_sky_wheel():
    """Build a wheel for Sky."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # prepare files
        tmp_dir = pathlib.Path(tmp_dir)
        (tmp_dir / 'sky').symlink_to(SKY_PACKAGE_PATH, target_is_directory=True)
        setup_files_dir = SKY_PACKAGE_PATH / 'setup_files'
        for f in setup_files_dir.iterdir():
            if f.is_file():
                shutil.copy(str(f), str(tmp_dir))

        # It is important to normalize the path, otherwise 'pip wheel' would
        # treat the directory as a file and generate an empty wheel.
        norm_path = str(tmp_dir) + os.sep
        try:
            # TODO(suquark): For python>=3.7, 'subprocess.run' supports capture
            # of the output.
            subprocess.run([
                'pip3', 'wheel', '--no-deps', norm_path, '--wheel-dir',
                str(tmp_dir)
            ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError('Fail to build pip wheel for Sky. '
                            f'Error message: {e.stderr.decode()}') from e

        wheel_name = (f'{_PACKAGE_WHEEL_NAME}-'
                  f'{version.parse(sky.__version__)}-*.whl')
        wheel_path = next(tmp_dir.glob(wheel_name))

        # Use a unique temporary dir per wheel hash, because there may be many
        # concurrent 'sky launch' happening.  The path should be stable if the
        # wheel content hash doesn't change.
        with open(wheel_path, 'rb') as f:
            contents = f.read()
        hash_of_latest_wheel = hashlib.md5(contents).hexdigest()

        wheel_dir = WHEEL_DIR / hash_of_latest_wheel
        wheel_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(wheel_path, wheel_dir)


def build_sky_wheel() -> Tuple[pathlib.Path, str]:
    """Build a wheel for Sky, or reuse a cached wheel.

    Caller is responsible for removing the wheel.

    Returns:
        A tuple of (wheel path, wheel hash):
        - wheel_path: A temporary path to a directory holding the wheel; path
        is guaranteed unique per wheel content hash.
        - wheel_hash: The wheel content hash.
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

        latest_wheel = _get_latest_built_wheel()
        wheel_hash = latest_wheel.parent.name
        cleanup_wheels_dir(WHEEL_DIR, latest_wheel.parent)

        # Use a unique temporary dir per wheel hash, because there may be many
        # concurrent 'sky launch' happening.  The path should be stable if the
        # wheel content hash doesn't change.
        temp_wheel_dir = pathlib.Path(
            tempfile.gettempdir()) / wheel_hash
        temp_wheel_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(latest_wheel, temp_wheel_dir)

    return temp_wheel_dir.absolute(), wheel_hash

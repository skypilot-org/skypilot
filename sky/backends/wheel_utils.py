"""Utils for building pip wheels.

This module is used for building the wheel for SkyPilot under `~/.sky/wheels`,
and the wheel will be used for installing the SkyPilot on the cluster nodes.

The generated folder is like:
    ~/.sky/wheels/<hash_of_wheel>/sky-<version>-py3-none-any.whl

Whenever a new wheel is built, the old ones will be removed.

The ray up yaml templates under sky/templates depend on the naming of the wheel.
"""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Tuple

import filelock
from packaging import version

import sky
from sky import sky_logging
from sky.backends import backend_utils

logger = sky_logging.init_logger(__name__)

# Local wheel path is same as the remote path.
WHEEL_DIR = pathlib.Path(os.path.expanduser(backend_utils.SKY_REMOTE_PATH))
_WHEEL_LOCK_PATH = WHEEL_DIR.parent / '.wheels_lock'
SKY_PACKAGE_PATH = pathlib.Path(sky.__file__).parent.parent / 'sky'

# NOTE: keep the same as setup.py's setuptools.setup(name=..., ...).
_PACKAGE_WHEEL_NAME = 'skypilot'
_WHEEL_PATTERN = (f'{_PACKAGE_WHEEL_NAME}-'
                  f'{version.parse(sky.__version__)}-*.whl')


def _get_latest_wheel_and_remove_all_others() -> pathlib.Path:
    wheel_name = (f'**/{_WHEEL_PATTERN}')
    try:
        latest_wheel = max(WHEEL_DIR.glob(wheel_name), key=os.path.getctime)
    except ValueError:
        raise FileNotFoundError(
            'Could not find built SkyPilot wheels with glob pattern '
            f'{wheel_name} under {WHEEL_DIR!r}') from None

    latest_wheel_dir_name = latest_wheel.parent
    # Cleanup older wheels.
    for f in WHEEL_DIR.iterdir():
        if f != latest_wheel_dir_name:
            if f.is_dir() and not f.is_symlink():
                shutil.rmtree(f, ignore_errors=True)
    return latest_wheel


def _build_sky_wheel():
    """Build a wheel for SkyPilot."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # prepare files
        tmp_dir = pathlib.Path(tmp_dir)
        sky_tmp_dir = tmp_dir / 'sky'
        sky_tmp_dir.mkdir()
        for item in SKY_PACKAGE_PATH.iterdir():
            target = sky_tmp_dir / item.name
            if item.name != '__init__.py':
                # We do not symlink `sky/__init__.py` as we need to
                # modify the commit hash in the file later.
                # Symlink other files/folders.
                target.symlink_to(item, target_is_directory=item.is_dir())
        setup_files_dir = SKY_PACKAGE_PATH / 'setup_files'

        setup_content = (setup_files_dir / 'setup.py').read_text()
        # Replace the package name with skypilot. This is important as the
        # package could be installed with pip install skypilot-nightly.
        setup_content = re.sub(r'\bname=[\'"](.*?)[\'"],',
                               f'name=\'{_PACKAGE_WHEEL_NAME}\',',
                               setup_content)
        (tmp_dir / 'setup.py').write_text(setup_content)

        for f in setup_files_dir.iterdir():
            if f.is_file() and f.name != 'setup.py':
                shutil.copy(str(f), str(tmp_dir))

        init_file_path = SKY_PACKAGE_PATH / '__init__.py'
        init_file_content = init_file_path.read_text()
        # Replace the commit hash with the current commit hash.
        init_file_content = re.sub(
            r'_SKYPILOT_COMMIT_SHA = [\'"](.*?)[\'"]',
            f'_SKYPILOT_COMMIT_SHA = \'{sky.__commit__}\'', init_file_content)
        (tmp_dir / 'sky' / '__init__.py').write_text(init_file_content)

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
            raise RuntimeError('Failed to build pip wheel for SkyPilot. '
                               f'Error message: {e.stderr.decode()}') from e

        try:
            wheel_path = next(tmp_dir.glob(_WHEEL_PATTERN))
        except StopIteration:
            raise RuntimeError(
                f'Failed to find pip wheel for SkyPilot under {tmp_dir} with '
                f'glob pattern {_WHEEL_PATTERN!r}. '
                f'Found: {list(map(str, tmp_dir.glob("*")))}.'
                'No wheel file is generated.') from None

        # Use a unique temporary dir per wheel hash, because there may be many
        # concurrent 'sky launch' happening.  The path should be stable if the
        # wheel content hash doesn't change.
        with open(wheel_path, 'rb') as f:
            contents = f.read()
        hash_of_latest_wheel = hashlib.md5(contents).hexdigest()

        wheel_dir = WHEEL_DIR / hash_of_latest_wheel
        wheel_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(wheel_path), wheel_dir)


def build_sky_wheel() -> Tuple[pathlib.Path, str]:
    """Build a wheel for SkyPilot, or reuse a cached wheel.

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
    with filelock.FileLock(_WHEEL_LOCK_PATH):  # pylint: disable=E0110
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

        latest_wheel = _get_latest_wheel_and_remove_all_others()

        wheel_hash = latest_wheel.parent.name

        # Use a unique temporary dir per wheel hash, because there may be many
        # concurrent 'sky launch' happening.  The path should be stable if the
        # wheel content hash doesn't change.
        temp_wheel_dir = pathlib.Path(tempfile.gettempdir()) / wheel_hash
        temp_wheel_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(latest_wheel, temp_wheel_dir)

    return temp_wheel_dir.absolute(), wheel_hash

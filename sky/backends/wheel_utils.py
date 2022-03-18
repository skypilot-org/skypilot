"""Utils for building sky pip wheels."""
from typing import Optional

import getpass
import os
import pathlib
import shutil
import subprocess
import tempfile

import filelock

import sky


def _cleanup_wheels_dir(wheel_dir: pathlib.Path,
                        latest_wheel: Optional[pathlib.Path]):
    # cleanup older wheels
    for f in wheel_dir.iterdir():
        if f != latest_wheel:
            if f.is_dir() and not f.is_symlink():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()

WHEEL_LOCK = '~/.sky/wheel.lock'
lock = filelock.FileLock(os.path.expanduser(WHEEL_LOCK))

def build_sky_wheel() -> pathlib.Path:
    """Build a wheel for sky. This works correctly only when sky is installed
    with development/editable mode."""
    with lock:
        # check if sky is installed under development mode.
        package_root = pathlib.Path(sky.__file__).parent.parent
        username = getpass.getuser()
        wheel_dir = pathlib.Path(tempfile.gettempdir()) / f'sky_wheels_{username}'

        if not wheel_dir.exists():
            wheel_dir.mkdir()
        # cleanup the directory
        # TODO(suquark): Cache built wheels to prevent rebuilding.
        # This may not be necessary because it's fast to build the wheel.
        _cleanup_wheels_dir(wheel_dir, None)

        # prepare files
        (wheel_dir / 'sky').symlink_to(package_root / 'sky',
                                    target_is_directory=True)
        setup_files_dir = package_root / 'sky' / 'setup_files'
        for f in setup_files_dir.iterdir():
            if f.is_file():
                shutil.copy(str(f), str(wheel_dir))

        # It is important to normalize the path, otherwise 'pip wheel' would treat
        # the directory as a file and generate an empty wheel.
        norm_path = str(wheel_dir) + os.sep
        try:
            # TODO(suquark): For python>=3.7, 'subprocess.run' supports capture of
            # the output.
            subprocess.run([
                'pip3', 'wheel', '--no-deps', norm_path, '--wheel-dir',
                str(wheel_dir)
            ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError('Fail to build pip wheel for Sky. '
                            f'Error message: {e.stderr.decode()}') from e
        try:
            latest_wheel = max(wheel_dir.glob('sky-*.whl'), key=os.path.getctime)
        except ValueError:
            raise FileNotFoundError('Could not find built Sky wheels.') from None
        _cleanup_wheels_dir(wheel_dir, latest_wheel)
        return wheel_dir.absolute()


if __name__ == '__main__':
    build_sky_wheel()

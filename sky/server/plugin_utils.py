"""Utils for building and managing plugin wheels.

This module provides utilities for building wheels from plugin packages
and uploading them to remote clusters.
"""
import hashlib
import os
import pathlib
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

import filelock

from sky import sky_logging
from sky.backends import wheel_utils
from sky.server import plugins

logger = sky_logging.init_logger(__name__)

# Directory for storing built plugin wheels
_PLUGIN_WHEEL_DIR = pathlib.Path(os.path.expanduser('~/.sky/plugins/wheels'))
_PLUGIN_WHEEL_LOCK_PATH = _PLUGIN_WHEEL_DIR.parent / '.plugin_wheels_lock'
# Remote directory for plugin wheels
_REMOTE_PLUGINS_WHEEL_DIR = '~/.sky/plugins/wheels'


def _get_package_name_from_path(package_path: str) -> str:
    """Extract package name from package path.

    Looks for the package name in pyproject.toml.
    Falls back to the directory name if not found.
    """
    package_path = os.path.expanduser(package_path)

    # Try pyproject.toml first
    pyproject_path = os.path.join(package_path, 'pyproject.toml')
    if os.path.exists(pyproject_path):
        try:
            # pylint: disable-next=import-outside-toplevel
            import tomllib
        except ImportError:
            # pylint: disable-next=import-outside-toplevel
            import tomli as tomllib  # type: ignore
        with open(pyproject_path, 'rb') as f:
            pyproject = tomllib.load(f)
            name = pyproject.get('project', {}).get('name')
            if name:
                return name

    # Fall back to directory name
    return os.path.basename(os.path.normpath(package_path))


def _compute_package_hash(package_path: str) -> str:
    """Compute a hash of the package contents for caching."""
    package_path = os.path.expanduser(package_path)
    hasher = hashlib.md5()

    for root, dirs, files in os.walk(package_path):
        # Skip common non-source directories
        dirs[:] = [
            d for d in dirs
            if d not in ('__pycache__', '.git', '.tox', 'dist', 'build',
                         '.eggs', 'venv',
                         '.venv') and not d.endswith('.egg-info')
        ]

        for filename in sorted(files):
            if filename.endswith(('.pyc', '.pyo', '.egg-info')):
                continue
            filepath = os.path.join(root, filename)
            try:
                with open(filepath, 'rb') as f:
                    hasher.update(f.read())
            except (IOError, OSError):
                # Skip files that can't be read
                pass

    return hasher.hexdigest()[:16]


def _build_plugin_wheel(package_path: str) -> pathlib.Path:
    """Build a wheel for a plugin package.

    Args:
        package_path: Path to the plugin package directory (containing
            pyproject.toml or setup.py).

    Returns:
        Path to the built wheel file.

    Raises:
        RuntimeError: If wheel building fails.
    """
    package_path = os.path.expanduser(package_path)

    if not os.path.isdir(package_path):
        raise ValueError(f'Plugin package path does not exist: {package_path}')

    # Check for pyproject.toml or setup.py
    has_pyproject = os.path.exists(os.path.join(package_path, 'pyproject.toml'))
    has_setup = os.path.exists(os.path.join(package_path, 'setup.py'))

    if not has_pyproject and not has_setup:
        raise ValueError(
            f'Plugin package at {package_path} must have a pyproject.toml '
            'or setup.py file.')

    package_name = _get_package_name_from_path(package_path)
    package_hash = _compute_package_hash(package_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        wheel_utils.run_pip_wheel(package_path, tmp_dir,
                                  f'plugin {package_name}')

        # Find the built wheel
        wheel_files = list(pathlib.Path(tmp_dir).glob('*.whl'))
        if not wheel_files:
            raise RuntimeError(
                f'No wheel file found after building plugin {package_name}')

        wheel_file = wheel_files[0]

        # Create output directory with hash
        output_dir = _PLUGIN_WHEEL_DIR / package_hash
        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy wheel to output directory
        dest_path = output_dir / wheel_file.name
        if not dest_path.exists():
            shutil.copy(wheel_file, dest_path)

        logger.debug(f'Built plugin wheel: {dest_path}')
        return dest_path


def _build_plugin_wheels() -> Tuple[Dict[str, pathlib.Path], str]:
    """Build wheels for all plugins that should be uploaded to controllers.

    Returns:
        A tuple of:
        - Dictionary mapping package names to wheel paths
        - Combined hash of all plugin wheels for caching
    """
    plugin_packages = plugins.get_plugin_packages()

    if not plugin_packages:
        return {}, ''

    # Ensure wheel directory exists
    _PLUGIN_WHEEL_DIR.mkdir(parents=True, exist_ok=True)

    wheels: Dict[str, pathlib.Path] = {}
    combined_hash = hashlib.md5()

    with filelock.FileLock(_PLUGIN_WHEEL_LOCK_PATH):
        for plugin_config in plugin_packages:
            # Only build wheels for plugins that should be uploaded
            # to controllers
            if not plugin_config.get('upload_to_controller', False):
                continue

            package_path = plugin_config.get('package')
            if not package_path:
                logger.warning(f'Plugin {plugin_config.get("class")} has '
                               'upload_to_controller=True but no package path '
                               'could be determined. Skipping wheel build.')
                continue

            package_path = os.path.expanduser(package_path)
            if not os.path.exists(package_path):
                logger.warning(
                    f'Plugin package path does not exist: {package_path}')
                continue

            package_name = _get_package_name_from_path(package_path)
            package_hash = _compute_package_hash(package_path)

            # Check if we already have a wheel for this hash
            cached_wheel_dir = _PLUGIN_WHEEL_DIR / package_hash
            existing_wheels = list(cached_wheel_dir.glob(
                '*.whl')) if cached_wheel_dir.exists() else []

            if existing_wheels:
                wheel_path = existing_wheels[0]
                logger.debug(f'Using cached wheel for {package_name}: '
                             f'{wheel_path}')
            else:
                wheel_path = _build_plugin_wheel(package_path)

            wheels[package_name] = wheel_path
            combined_hash.update(package_hash.encode())

    # Return empty hash if no wheels were built
    if not wheels:
        return {}, ''

    return wheels, combined_hash.hexdigest()[:16]


def get_plugin_mounts_and_commands() -> Tuple[Dict[str, str], str]:
    """Get file mounts and installation commands for plugin wheels.

    This function builds the plugin wheels once and returns both the file
    mounts for uploading them to remote clusters and the shell commands
    for installing them. This ensures consistency between the uploaded
    wheel paths and the installation commands.

    Returns:
        A tuple of:
        - Dictionary mapping remote paths to local paths for plugin wheels
        - Shell commands to install all plugin wheels
    """
    # pylint: disable-next=import-outside-toplevel
    from sky.skylet import constants

    plugin_packages = plugins.get_plugin_packages()

    if not plugin_packages:
        return {}, ''

    # Check if any plugins should be uploaded to controllers
    should_upload = any(
        p.get('upload_to_controller', False) for p in plugin_packages)
    if not should_upload:
        return {}, ''

    # Build wheels once to ensure consistency between file mounts and commands
    wheels, _ = _build_plugin_wheels()
    if not wheels:
        return {}, ''

    file_mounts: Dict[str, str] = {}
    commands = []

    for _, wheel_path in wheels.items():
        # File mount: upload the wheel directory to the remote cluster
        # Keep ~ in the remote path - file mount system will handle expansion
        remote_dir = (f'{_REMOTE_PLUGINS_WHEEL_DIR}/'
                      f'{wheel_path.parent.name}')
        file_mounts[remote_dir] = str(wheel_path.parent)

        # Installation command: install the wheel on the remote cluster
        # Use ~ which will be expanded by the shell when the command runs.
        remote_wheel_path = (f'{_REMOTE_PLUGINS_WHEEL_DIR}/'
                             f'{wheel_path.parent.name}/{wheel_path.name}')
        # Install the wheel using uv pip
        # Note: We don't quote the path so that ~ gets expanded by the shell
        install_cmd = (f'{constants.SKY_UV_PIP_CMD} install '
                       f'{remote_wheel_path}')
        commands.append(install_cmd)

    return file_mounts, ' && '.join(commands)


def cleanup_stale_plugin_wheels(keep_hashes: Optional[List[str]] = None):
    """Remove stale plugin wheel directories.

    Args:
        keep_hashes: List of hash prefixes to keep. If None, keeps all
            wheels for currently configured plugins that should be uploaded.
    """
    if not _PLUGIN_WHEEL_DIR.exists():
        return

    if keep_hashes is None:
        # Compute hashes for current plugins that should be uploaded
        plugin_packages = plugins.get_plugin_packages()
        keep_hashes = []
        for plugin_config in plugin_packages:
            if not plugin_config.get('upload_to_controller', False):
                continue
            package_path = plugin_config.get('package')
            if package_path:
                package_path = os.path.expanduser(package_path)
                if os.path.exists(package_path):
                    keep_hashes.append(_compute_package_hash(package_path))

    with filelock.FileLock(_PLUGIN_WHEEL_LOCK_PATH):
        for item in _PLUGIN_WHEEL_DIR.iterdir():
            if item.is_dir() and item.name not in keep_hashes:
                shutil.rmtree(item, ignore_errors=True)
                logger.debug(f'Removed stale plugin wheel directory: {item}')


def get_filtered_plugins_config_path() -> Optional[str]:
    """Create a filtered plugins.yaml with only plugins that should be uploaded.

    The controller should only attempt to load plugins that have their packages
    uploaded. Plugins without upload_to_controller=True are intended
    for API server use only and should not be loaded on the controller.

    Returns:
        Path to a temporary file containing the filtered plugins config,
        or None if no plugins should be uploaded.
    """
    # pylint: disable-next=import-outside-toplevel
    from sky.utils import yaml_utils

    plugin_packages = plugins.get_plugin_packages()
    if not plugin_packages:
        return None

    # Filter to only include plugins that should be uploaded to controllers
    plugins_to_upload = [
        p for p in plugin_packages if p.get('upload_to_controller', False)
    ]

    if not plugins_to_upload:
        # No plugins should be uploaded - don't upload any config
        return None

    # Create a filtered config, removing internal fields that aren't needed
    # on the controller (package, upload_to_controller)
    filtered_plugins = []
    for plugin_config in plugins_to_upload:
        filtered_plugin = {
            'class': plugin_config['class'],
        }
        if 'parameters' in plugin_config:
            filtered_plugin['parameters'] = plugin_config['parameters']
        filtered_plugins.append(filtered_plugin)

    filtered_config = {'plugins': filtered_plugins}

    # Write to a temporary file
    # Using delete=False so the file persists until the controller upload
    with tempfile.NamedTemporaryFile(mode='w',
                                     suffix='_plugins.yaml',
                                     prefix='sky_filtered_',
                                     delete=False) as temp_file:
        yaml_utils.dump_yaml(temp_file.name, filtered_config)
        return temp_file.name

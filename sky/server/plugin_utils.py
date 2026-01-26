"""Utils for managing plugin wheels.

This module provides utilities for uploading prebuilt plugin wheels
to remote clusters.
"""
import os
import pathlib
import tempfile
from typing import Dict, Optional, Tuple

from sky import sky_logging
from sky.server import plugins

logger = sky_logging.init_logger(__name__)

# Remote directory for plugin wheels
_REMOTE_PLUGINS_WHEEL_DIR = '~/.sky/plugins/wheels'


def get_plugin_mounts_and_commands() -> Tuple[Dict[str, str], str]:
    """Get file mounts and installation commands for plugin wheels.

    This function reads the controller wheel directory path from the plugin
    config, finds all .whl files in that directory, and returns both the file
    mounts for uploading them to remote clusters and the shell commands for
    installing them.

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

    # Get the controller wheel directory path from the top-level config
    wheel_dir_str = plugins.get_controller_wheel_path()
    if not wheel_dir_str:
        logger.warning('Some plugins have upload_to_controller=True but '
                       'controller_wheel_path is not specified in the config. '
                       'Skipping wheel upload.')
        return {}, ''

    # Expand user path and validate
    wheel_dir = pathlib.Path(os.path.expanduser(wheel_dir_str))
    if not wheel_dir.exists():
        logger.warning(
            f'Controller wheel directory does not exist: {wheel_dir}')
        return {}, ''

    if not wheel_dir.is_dir():
        logger.warning(f'Controller wheel path is not a directory: {wheel_dir}')
        return {}, ''

    # Find all .whl files in the directory
    wheel_files = list(wheel_dir.glob('*.whl'))
    if not wheel_files:
        logger.debug(
            f'No .whl files found in controller wheel directory: {wheel_dir}')
        return {}, ''

    file_mounts: Dict[str, str] = {}
    commands = []

    for wheel_path in wheel_files:
        # File mount: upload the wheel file directly to the remote cluster
        # Use the wheel filename as the remote path
        remote_wheel_path = (f'{_REMOTE_PLUGINS_WHEEL_DIR}/'
                             f'{wheel_path.name}')
        file_mounts[remote_wheel_path] = str(wheel_path)

        # Installation command: install the wheel on the remote cluster
        # Use ~ which will be expanded by the shell when the command runs.
        # Note: We don't quote the path so that ~ gets expanded by the shell
        install_cmd = (f'{constants.SKY_UV_PIP_CMD} install '
                       f'{remote_wheel_path}')
        commands.append(install_cmd)

    logger.info(f'Found {len(wheel_files)} wheel file(s) in {wheel_dir}, '
                f'uploading and installing them on controller.')
    return file_mounts, ' && '.join(commands)


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
    # on the controller (upload_to_controller)
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

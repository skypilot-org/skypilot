"""Utils for managing plugin wheels.

This module provides utilities for uploading prebuilt plugin wheels
to remote clusters.
"""
import os
import pathlib
from typing import Dict, Optional, Tuple

from sky import sky_logging
from sky.server import plugins
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# Remote directory for plugin wheels
_REMOTE_PLUGINS_WHEEL_DIR = '~/.sky/plugins/wheels'


def get_plugin_mounts_and_commands() -> Tuple[Dict[str, str], str]:
    """Get file mounts and installation commands for plugin wheels.

    This function reads the controller wheel directory path from the plugin
    config (plugins.yaml), finds all .whl files in that directory,
    and returns both the file mounts for uploading them to remote clusters and
    the shell commands for installing them.

    Returns:
        A tuple of:
        - Dictionary mapping remote paths to local paths for plugin wheels
        - Shell commands to install all plugin wheels
    """

    remote_plugin_packages = plugins.get_remote_plugin_packages()

    if not remote_plugin_packages:
        return {}, ''

    # Get the controller wheel directory path from the plugin config
    wheel_dir_str = plugins.get_remote_controller_wheel_path()
    if not wheel_dir_str:
        logger.warning(
            'Remote plugins are specified but '
            'controller_wheel_path is not specified in plugins.yaml. '
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

    return file_mounts, ' && '.join(commands)


def get_filtered_plugins_config_path() -> Optional[str]:
    """Return the path to remote_plugins.yaml if it exists.

    The controller should only attempt to load plugins that are specified in
    remote_plugins.yaml. Plugins in plugins.yaml are intended for API server
    use only and should not be loaded on the controller.

    Returns:
        Path to the remote_plugins.yaml file if it exists and contains plugins,
        or None if no remote plugins are configured.
    """
    remote_plugin_packages = plugins.get_remote_plugin_packages()
    if not remote_plugin_packages:
        return None

    # Get the path to remote_plugins.yaml
    return plugins.get_remote_plugins_config_path()

"""Utils for managing plugin wheels.

This module provides utilities for uploading prebuilt plugin wheels
to remote clusters.
"""
import os
import pathlib
import shlex
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.server import plugins
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# Remote directory for plugin wheels
_REMOTE_PLUGINS_WHEEL_DIR = '~/.sky/plugins/wheels'
_REMOTE_STAMP_FILE = '~/.sky/plugins/.installed_wheels'
_REMOTE_INSTALL_LOCK = '~/.sky/plugins/.install.lock'
_INSTALL_LOCK_TIMEOUT = 600


def _wheel_name_prefix(wheel_filename: str) -> str:
    """Return the ``{name}-`` prefix of a wheel filename.

    PEP 427 wheel filenames are ``{name}-{version}(-{build})?-{py}-{abi}-
    {platform}.whl`` with hyphens in the distribution name replaced by
    underscores, so the name portion is whatever precedes the first hyphen.
    """
    return wheel_filename.split('-', 1)[0] + '-'


def _build_guarded_install_script(wheels: List[Tuple[str, str]]) -> str:
    """Build a shell script that installs each wheel at most once.

    For each wheel, the script:
    1. Takes an exclusive flock so concurrent launches don't race.
    2. Skips the install if the exact wheel filename is already recorded in
       the stamp file.
    3. On install, replaces any prior entry for the same package name in the
       stamp file and prunes stale wheel files for that package.

    Args:
        wheels: list of ``(wheel_filename, remote_path)`` pairs.
    """
    install_blocks = []
    for wheel_name, remote_wheel in wheels:
        q_wheel = shlex.quote(wheel_name)
        q_prefix = shlex.quote(_wheel_name_prefix(wheel_name))
        # remote_wheel starts with '~' which must be left unquoted so the
        # shell expands it; the filename portion cannot contain shell
        # metacharacters by PEP 427.
        install_blocks.append(f"""\
  if grep -Fxq {q_wheel} "$_sky_stamp"; then
    echo "Plugin wheel {wheel_name} already installed, skipping."
  else
    echo "Installing plugin wheel {wheel_name}..."
    {constants.SKY_UV_PIP_CMD} install {remote_wheel}
    _sky_record {q_prefix} {q_wheel}
  fi""")

    body = '\n'.join(install_blocks)
    return f"""(
  set -e
  mkdir -p ~/.sky/plugins {_REMOTE_PLUGINS_WHEEL_DIR}
  exec 9>{_REMOTE_INSTALL_LOCK}
  flock -w {_INSTALL_LOCK_TIMEOUT} 9 || {{
    echo "Failed to acquire plugin install lock within \
{_INSTALL_LOCK_TIMEOUT}s" >&2
    exit 1
  }}
  _sky_stamp={_REMOTE_STAMP_FILE}
  touch "$_sky_stamp"
  _sky_record() {{
    local _t
    _t=$(mktemp "$_sky_stamp.XXXXXX")
    grep -v "^$1" "$_sky_stamp" > "$_t" || true
    echo "$2" >> "$_t"
    mv "$_t" "$_sky_stamp"
    find {_REMOTE_PLUGINS_WHEEL_DIR} -maxdepth 1 \\
      -name "$1*.whl" ! -name "$2" -delete 2>/dev/null || true
  }}
{body}
)"""


def get_plugin_mounts_and_commands() -> Tuple[Dict[str, str], str]:
    """Get file mounts and installation commands for plugin wheels.

    This function reads the controller wheel directory path from the plugin
    config (plugins.yaml), finds all .whl files in that directory,
    and returns both the file mounts for uploading them to remote clusters and
    the shell commands for installing them.

    The install commands are idempotent: wheels whose filename is already
    recorded on the controller (in ``~/.sky/plugins/.installed_wheels``) are
    skipped, and concurrent installs are serialized via ``flock`` so that
    parallel launches don't race through the install step and break in-flight
    plugin imports with partial site-packages state.

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
    wheels: List[Tuple[str, str]] = []

    for wheel_path in wheel_files:
        # File mount: upload the wheel file directly to the remote cluster
        # Use the wheel filename as the remote path
        remote_wheel_path = (f'{_REMOTE_PLUGINS_WHEEL_DIR}/'
                             f'{wheel_path.name}')
        file_mounts[remote_wheel_path] = str(wheel_path)
        wheels.append((wheel_path.name, remote_wheel_path))

    return file_mounts, _build_guarded_install_script(wheels)


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

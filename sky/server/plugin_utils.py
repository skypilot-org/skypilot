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
# Stamp file listing wheel filenames already installed on the controller.
_REMOTE_STAMP_FILE = '~/.sky/plugins/.installed_wheels'
# Lock serializing concurrent plugin installs across parallel launches on the
# same controller. Without this, two `sky jobs launch` invocations racing
# through the run phase can half-rewrite site-packages while other plugin
# imports are in flight, producing transient ImportError.
_REMOTE_INSTALL_LOCK = '~/.sky/plugins/.install.lock'
# Timeout (seconds) to wait for the install lock before giving up.
_INSTALL_LOCK_TIMEOUT = 600


def _wheel_name_prefix(wheel_filename: str) -> str:
    """Return the ``{name}-`` prefix of a wheel filename.

    PEP 427 wheel filenames are ``{name}-{version}(-{build})?-{py}-{abi}-
    {platform}.whl`` with hyphens in the distribution name replaced by
    underscores, so the name portion is whatever precedes the first hyphen.
    """
    return wheel_filename.split('-', 1)[0] + '-'


def _build_guarded_install_script(entries: List[Tuple[str, str, str]]) -> str:
    """Build a shell script that installs each wheel at most once.

    For each wheel, the script:
    1. Takes an exclusive flock so concurrent launches don't race.
    2. Skips the install if the exact wheel filename is already recorded in
       the stamp file.
    3. On install, replaces any prior entry for the same package name in the
       stamp file and prunes stale wheel files for that package.

    Args:
        entries: list of ``(wheel_filename, package_prefix, remote_path)``.
    """
    install_blocks = []
    for wheel_name, pkg_prefix, remote_wheel in entries:
        q_wheel = shlex.quote(wheel_name)
        q_prefix = shlex.quote(pkg_prefix)
        q_prefix_glob = shlex.quote(f'{pkg_prefix}*.whl')
        # remote_wheel starts with '~' which must be left unquoted so the
        # shell expands it; the filename portion cannot contain shell
        # metacharacters by PEP 427.
        install_blocks.append(f"""\
  if grep -Fxq {q_wheel} "$_sky_stamp"; then
    echo "Plugin wheel {wheel_name} already installed, skipping."
  else
    echo "Installing plugin wheel {wheel_name}..."
    {constants.SKY_UV_PIP_CMD} install {remote_wheel}
    _sky_tmp=$(mktemp "$_sky_stamp.XXXXXX")
    grep -v "^"{q_prefix} "$_sky_stamp" > "$_sky_tmp" || true
    echo {q_wheel} >> "$_sky_tmp"
    mv "$_sky_tmp" "$_sky_stamp"
    find {_REMOTE_PLUGINS_WHEEL_DIR} -maxdepth 1 \\
      -name {q_prefix_glob} ! -name {q_wheel} -delete 2>/dev/null || true
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
    entries: List[Tuple[str, str, str]] = []

    for wheel_path in wheel_files:
        # File mount: upload the wheel file directly to the remote cluster
        # Use the wheel filename as the remote path
        remote_wheel_path = (f'{_REMOTE_PLUGINS_WHEEL_DIR}/'
                             f'{wheel_path.name}')
        file_mounts[remote_wheel_path] = str(wheel_path)
        entries.append((wheel_path.name, _wheel_name_prefix(wheel_path.name),
                        remote_wheel_path))

    return file_mounts, _build_guarded_install_script(entries)


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

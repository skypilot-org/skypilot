"""API versioning module."""

import contextvars
import functools
import re
from typing import Callable, Literal, Mapping, NamedTuple, Optional, Tuple

import colorama
from packaging import version as version_lib

import sky
from sky import exceptions
from sky import sky_logging
from sky.server import constants
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

CLIENT_TOO_OLD_ERROR = (
    f'{colorama.Fore.YELLOW}Your SkyPilot client version is too old:'
    '{remote_version}\n'
    f'{colorama.Style.RESET_ALL}'
    'The server is running on {local_version} and the minimum compatible '
    'version is {min_version}.\n'
    f'Upgrade your client with:\n{colorama.Fore.YELLOW}'
    '{command}'
    f'{colorama.Style.RESET_ALL}')
SERVER_TOO_OLD_ERROR = (
    f'{colorama.Fore.YELLOW}Your SkyPilot API server version is too old: '
    '{remote_version}\n'
    f'{colorama.Style.RESET_ALL}'
    'The client is running on {local_version} and the minimum compatible '
    'version is {min_version}.\n'
    'Contact your administrator to upgrade the remote API server or downgrade '
    f'your client with:\n{colorama.Fore.YELLOW}'
    '{command}'
    f'{colorama.Style.RESET_ALL}')

# SkyPilot dev version.
DEV_VERSION = '1.0.0-dev0'

_REMOTE_TO_ERROR = {
    'client': CLIENT_TOO_OLD_ERROR,
    'server': SERVER_TOO_OLD_ERROR,
}

# Context-local (thread or cooroutine) remote API version, captured during
# communication with the remote peer.
_remote_api_version: contextvars.ContextVar[Optional[int]] = \
    contextvars.ContextVar('remote_api_version', default=None)
_remote_version: contextvars.ContextVar[str] = \
    contextvars.ContextVar('remote_version', default='unknown')
_reminded_for_minor_version_upgrade = False


def get_remote_api_version() -> Optional[int]:
    return _remote_api_version.get()


def set_remote_api_version(api_version: int) -> None:
    _remote_api_version.set(api_version)


def get_remote_version() -> str:
    return _remote_version.get()


def set_remote_version(version: str) -> None:
    _remote_version.set(version)


class VersionInfo(NamedTuple):
    api_version: int
    version: str
    error: Optional[str] = None


def check_compatibility_at_server(
        client_headers: Mapping[str, str]) -> Optional[VersionInfo]:
    """Check API compatibility between client and server."""
    return _check_version_compatibility(client_headers, 'client')


def check_compatibility_at_client(
        server_headers: Mapping[str, str]) -> Optional[VersionInfo]:
    """Check API compatibility between client and server."""
    return _check_version_compatibility(server_headers, 'server')


def _check_version_compatibility(
        remote_headers: Mapping[str, str],
        remote_type: Literal['client', 'server']) -> Optional[VersionInfo]:
    """Check API compatibility between client and server.

    This function can be called at both client and server side, where the
    headers should contain the version info of the remote.

    Args:
        remote_headers: The headers of the request/response sent from the
            remote.
        remote_type: The type of the remote, used to determine the error
            message. Valid options are 'client' and 'server'.

    Returns:
        The version info of the remote, None if the version info is not found
        in the headers for backward compatibility.
    """
    api_version_str = remote_headers.get(constants.API_VERSION_HEADER)
    version = remote_headers.get(constants.VERSION_HEADER)
    if version is None or api_version_str is None:
        return None
    try:
        api_version = int(api_version_str)
    except ValueError:
        # The future change is expected to not break the compatibility of this
        # header, so we are encountering a bug or a malicious request here,
        # just raise an error.
        raise ValueError(
            f'Header {constants.API_VERSION_HEADER}: '
            f'{api_version_str} is not a valid API version.') from None

    if api_version < constants.MIN_COMPATIBLE_API_VERSION:
        if remote_type == 'server':
            # Hint the user to downgrade to client to the remote server server.
            server_version, server_commit = parse_readable_version(version)
            command = install_version_command(server_version, server_commit)
        else:
            # Hint the client to upgrade to upgrade the server version
            command = install_version_command(sky.__version__, sky.__commit__)
        return VersionInfo(api_version=api_version,
                           version=version,
                           error=_REMOTE_TO_ERROR[remote_type].format(
                               remote_version=version,
                               local_version=get_local_readable_version(),
                               min_version=constants.MIN_COMPATIBLE_VERSION,
                               command=command,
                           ))

    if remote_type == 'server':
        # Only print the reminder at client-side.
        _remind_minor_version_upgrade(version)

    return VersionInfo(api_version=api_version, version=version)


def get_local_readable_version() -> str:
    """Get the readable version of the SkyPilot code loaded in current process.

    For dev version, the version is formatted as: 1.0.0-dev0 (commit: 1234567)
    to make it meaningful for users.
    """
    if sky.__version__ == DEV_VERSION:
        return f'{sky.__version__} (commit: {sky.__commit__})'
    else:
        return sky.__version__


def parse_readable_version(version: str) -> Tuple[str, Optional[str]]:
    """Parse a readable produced by get_local_readable_version.

    Args:
        version: The version string to parse.

    Returns:
        A tuple of (version, optional_commit) where:
        - version: The base version string (e.g., "1.0.0-dev0")
        - optional_commit: The commit hash if present, None otherwise
    """
    # Check if this is a dev version with commit info
    # Format: "1.0.0-dev0 (commit: 1234567)"
    commit_pattern = r'^(.+) \(commit: ([a-f0-9]+)\)$'
    match = re.match(commit_pattern, version)

    if match:
        base_version = match.group(1)
        commit = match.group(2)
        return base_version, commit
    else:
        # Regular version without commit info
        return version, None


def install_version_command(version: str, commit: Optional[str] = None) -> str:
    if version == DEV_VERSION:
        if commit is not None:
            return ('pip install git+https://github.com/skypilot-org/skypilot@'
                    f'{commit}')
    elif 'dev' in version:
        return f'pip install -U "skypilot-nightly=={version}"'
    return f'pip install -U "skypilot=={version}"'


def _remind_minor_version_upgrade(remote_version: str) -> None:
    """Remind the user to upgrade the CLI/SDK."""
    # Only print the reminder once per process.
    global _reminded_for_minor_version_upgrade
    if _reminded_for_minor_version_upgrade:
        return
    # Skip for dev versions.
    if 'dev' in sky.__version__ or 'dev' in remote_version:
        return

    # Remove the commit info if any.
    remote_base_version, _ = parse_readable_version(remote_version)

    # Parse semver for both local and remote versions
    try:
        local = version_lib.parse(sky.__version__)
        remote = version_lib.parse(remote_base_version)

        # Check if local version is behind remote version, ignore patch version.
        if (local.major, local.minor) < (remote.major, remote.minor):
            logger.warning(
                f'{colorama.Fore.YELLOW}The SkyPilot API server is running in '
                f'version {remote_version}, which is newer than your client '
                f'version {sky.__version__}. The compatibility for your '
                f'current version might be dropped in the next server upgrade.'
                f'\nConsider upgrading your client with:\n'
                f'{install_version_command(remote_version)}'
                f'{colorama.Style.RESET_ALL}')
            _reminded_for_minor_version_upgrade = True
    except version_lib.InvalidVersion:
        # Skip for non-valid semver (probabely a dev version)
        pass


# TODO(aylei): maybe we can use similiar approach to mark a new argument can
# only be used in the new server version.
def minimal_api_version(min_version: int) -> Callable:
    """Decorator to enforce a minimum remote API version for an SDK function.

    New SDK method must be decorated with this decorator to make sure it raises
    an readable error when the remote server is not upgraded.

    Args:
        min_version: The minimum remote API version required to call the
        function.

    Returns:
        A decorator function that checks API version before execution.

    Raises:
        APINotSupportedError: If the remote API version is below the minimum
            required.
    """

    def decorator(func: Callable) -> Callable:

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            remote_api_version = get_remote_api_version()
            if remote_api_version is None:
                return func(*args, **kwargs)
            if remote_api_version < min_version:
                with ux_utils.print_exception_no_traceback():
                    hint = 'Please upgrade the remote server.'
                    # The client runs in a released version, do better hint.
                    if 'dev' not in sky.__version__:
                        hint = (
                            f'Upgrade the remote server to {sky.__version__} '
                            'and re-run the command.')
                    raise exceptions.APINotSupportedError(
                        f'Function {func.__name__} is introduced after the '
                        f'remote server version {get_remote_version()!r} is '
                        f'released. {hint}')
            return func(*args, **kwargs)

        return wrapper

    return decorator


def check_recipe_client_version(task: str) -> None:
    """Reject recipe launches from clients older than the minimum version.

    An old client that doesn't understand the recipes: prefix will treat it
    as a literal shell command, producing a task YAML with
    ``run: recipes:<name>``. We detect this pattern in the raw YAML string
    and reject with a helpful error.

    This is called during request execution (not in the endpoint handler) so
    that the error propagates through the normal request polling path, which
    old clients already handle.
    """
    if not re.search(r'^run:\s*recipes:', task, re.MULTILINE):
        return

    client_api_version = get_remote_api_version()
    if (client_api_version is None or
            client_api_version < constants.MIN_RECIPE_LAUNCH_API_VERSION):
        raise RuntimeError(
            'Launching recipes requires a newer SkyPilot client. '
            'Please upgrade your SkyPilot installation.')

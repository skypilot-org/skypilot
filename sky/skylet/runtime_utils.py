"""Runtime utilities for SkyPilot."""
import os
import pathlib

from sky.skylet import constants


def get_runtime_dir_path(path_suffix: str = '') -> str:
    """Get an expanded path within the SkyPilot runtime directory.

    Args:
        path_suffix: Path suffix to join with the runtime dir
        (e.g., '.sky/jobs.db').

    Returns:
        The full expanded path.
    """
    runtime_dir = os.path.expanduser(
        os.environ.get(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, '~'))
    if path_suffix:
        return os.path.join(runtime_dir, path_suffix)
    return runtime_dir


def expanduser(path: str) -> str:
    """Like os.path.expanduser, but roots '~' at the SkyPilot runtime dir.

    When the SKY_RUNTIME_DIR environment variable is set, a leading '~' (with
    no explicit user name) resolves to the runtime directory instead of $HOME.
    Falls back to os.path.expanduser otherwise, so behavior is unchanged when
    the environment variable is unset.

    Use this for paths that belong to a SkyPilot runtime instance (databases,
    server state, locks, logs), NOT for user-owned paths such as credentials
    or paths that are sent to another machine for resolution there.
    """
    if os.environ.get(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY) is None:
        return os.path.expanduser(path)
    if path == '~':
        return get_runtime_dir_path()
    if path.startswith('~/'):
        return get_runtime_dir_path(path[2:])
    return os.path.expanduser(path)


def expanduser_path(path: pathlib.Path) -> pathlib.Path:
    """pathlib variant of expanduser (see above)."""
    return pathlib.Path(expanduser(str(path)))


def runtime_tilde_path(path: str) -> str:
    """Anchors a '~/...' path at SKY_RUNTIME_DIR when set; else returns as-is.

    For '~'-relative SkyPilot path constants: when no runtime dir is
    configured, the constant keeps its portable '~' form and callers expand
    it at use time as before. When a runtime dir is configured, the path is
    anchored there, so the subsequent expanduser calls become no-ops and the
    path stays consistent everywhere it is used within this process.
    """
    if os.environ.get(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY) is None:
        return path
    return expanduser(path)

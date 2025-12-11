"""Runtime utilities for SkyPilot."""
import os

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

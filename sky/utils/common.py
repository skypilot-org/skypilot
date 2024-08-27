"""Common enumerators and classes."""

import collections
import enum
import importlib

from sky import sky_logging
from sky.utils import common_utils


class StatusRefreshMode(enum.Enum):
    """The mode of refreshing the status of a cluster."""

    NONE = 'NONE'
    # Automatically refresh when needed, e.g., autostop is set or the cluster
    # is a spot instance.
    AUTO = 'AUTO'
    FORCE = 'FORCE'


# Constants: minimize what target?
class OptimizeTarget(enum.Enum):
    COST = 0
    TIME = 1


RealtimeGpuAvailability = collections.namedtuple(
    'RealtimeGpuAvailability', ['gpu', 'counts', 'capacity', 'available'])

SKY_SERVE_CONTROLLER_NAME: str = (
    f'sky-serve-controller-{common_utils.get_user_hash()}')

# Add user hash so that two users don't have the same controller VM on
# shared-account clouds such as GCP.
JOB_CONTROLLER_NAME: str = (
    f'sky-jobs-controller-{common_utils.get_user_hash()}')
LEGACY_JOB_CONTROLLER_NAME: str = (
    f'sky-spot-controller-{common_utils.get_user_hash()}')


def reload():
    # When a user request is sent to api server, it changes the user hash in the
    # env vars, but since controller_utils is imported before the env vars are
    # set, it doesn't get updated. So we need to reload it here.
    # pylint: disable=import-outside-toplevel
    from sky.utils import controller_utils
    from sky import skypilot_config
    global SKY_SERVE_CONTROLLER_NAME
    global JOB_CONTROLLER_NAME
    global LEGACY_JOB_CONTROLLER_NAME
    SKY_SERVE_CONTROLLER_NAME = (
        f'sky-serve-controller-{common_utils.get_user_hash()}')
    JOB_CONTROLLER_NAME = (
        f'sky-jobs-controller-{common_utils.get_user_hash()}')
    LEGACY_JOB_CONTROLLER_NAME = (
        f'sky-spot-controller-{common_utils.get_user_hash()}')
    importlib.reload(controller_utils)
    importlib.reload(skypilot_config)

    # Make sure the logger takes the new environment variables. This is
    # necessary because the logger is initialized before the environment
    # variables are set, such as SKYPILOT_DEBUG.
    sky_logging.reload_logger()

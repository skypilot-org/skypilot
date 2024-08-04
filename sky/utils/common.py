"""Common enumerators and classes."""

import enum

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


SKY_SERVE_CONTROLLER_NAME: str = (
    f'sky-serve-controller-{common_utils.get_user_hash()}')

# Add user hash so that two users don't have the same controller VM on
# shared-account clouds such as GCP.
JOB_CONTROLLER_NAME: str = (
    f'sky-jobs-controller-{common_utils.get_user_hash()}')
LEGACY_JOB_CONTROLLER_NAME: str = (
    f'sky-spot-controller-{common_utils.get_user_hash()}')

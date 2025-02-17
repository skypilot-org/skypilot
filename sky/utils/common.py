"""Common enumerators and classes."""

import enum
from typing import Optional

from sky.utils import common_utils

# We use the user hash (machine-specific) for the controller name. It will be
# the same across the whole lifecycle of the server, including:
# 1. all requests, because this global variable is set once during server
#    starts.
# 2. SkyPilot API server restarts, as long as the `~/.sky` folder is persisted
#    and the env var set during starting the server is the same.
# This behavior is the same for the local API server (where SERVER_ID is the
# same as the normal user hash). This ensures backwards-compatibility with jobs
# controllers from before #4660.
SERVER_ID = common_utils.get_user_hash()
SKY_SERVE_CONTROLLER_NAME = f'sky-serve-controller-{SERVER_ID}'
JOB_CONTROLLER_NAME = f'sky-jobs-controller-{SERVER_ID}'


class StatusRefreshMode(enum.Enum):
    """The mode of refreshing the status of a cluster."""
    NONE = 'NONE'
    """Do not refresh any clusters."""
    AUTO = 'AUTO'
    """Only refresh clusters if their autostop is set or have spot instances."""
    FORCE = 'FORCE'
    """Enforce refreshing all clusters."""


# Constants: minimize what target?
class OptimizeTarget(enum.Enum):
    COST = 0
    TIME = 1
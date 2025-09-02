"""Common enumerators and classes."""

import contextlib
import enum
import os
from typing import Generator

from sky import models
from sky.skylet import constants
from sky.utils import common_utils

SKY_SERVE_CONTROLLER_PREFIX: str = 'sky-serve-controller-'
JOB_CONTROLLER_PREFIX: str = 'sky-jobs-controller-'

# We use the user hash (machine-specific) for the controller name. It will be
# the same across the whole lifecycle of the server, including:
# 1. all requests, because all the server processes share the same user hash
#    cache file.
# 2. SkyPilot API server restarts, because the API server will restore the
#    user hash from the global user state db on startup.
# 3. Potential multiple server replicas, because multiple server replicas of
#    a same deployment will share the same global user state db.
# This behavior is the same for the local API server (where SERVER_ID is the
# same as the normal user hash). This ensures backwards-compatibility with jobs
# controllers from before #4660.
SERVER_ID: str
SKY_SERVE_CONTROLLER_NAME: str
JOB_CONTROLLER_NAME: str


def refresh_server_id() -> None:
    """Refresh the server id.

    This function is used to ensure the server id is read from the authorative
    source.
    """
    global SERVER_ID
    global SKY_SERVE_CONTROLLER_NAME
    global JOB_CONTROLLER_NAME
    SERVER_ID = common_utils.get_user_hash()
    SKY_SERVE_CONTROLLER_NAME = f'{SKY_SERVE_CONTROLLER_PREFIX}{SERVER_ID}'
    JOB_CONTROLLER_NAME = f'{JOB_CONTROLLER_PREFIX}{SERVER_ID}'


refresh_server_id()


@contextlib.contextmanager
def with_server_user() -> Generator[None, None, None]:
    """Temporarily set the user hash to common.SERVER_ID."""
    old_env_user_hash = os.getenv(constants.USER_ID_ENV_VAR)
    # TODO(zhwu): once we have fully moved our code to use `get_current_user()`
    # instead of `common_utils.get_user_hash()`, we can remove the env override.
    os.environ[constants.USER_ID_ENV_VAR] = SERVER_ID
    common_utils.set_current_user(models.User.get_current_user())
    try:
        yield
    finally:
        if old_env_user_hash is not None:
            os.environ[constants.USER_ID_ENV_VAR] = old_env_user_hash
        else:
            os.environ.pop(constants.USER_ID_ENV_VAR)
        common_utils.set_current_user(models.User.get_current_user())


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

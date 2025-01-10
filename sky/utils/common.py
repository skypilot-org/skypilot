"""Common enumerators and classes."""

import enum
import importlib

from sky import sky_logging
from sky.utils import common_utils

SKY_SERVE_CONTROLLER_PREFIX: str = 'sky-serve-controller-'
JOB_CONTROLLER_PREFIX: str = 'sky-jobs-controller-'
SERVER_ID_CONNECTOR: str = '-remote-'
# We use the user hash (machine-specific) hash of the server to determine if a
# SkyPilot API server is started by the same user. It will be the same across
# the whole lifecycle of the server, including:
# 1. all requests, because this global variable is set once during server
#    starts.
# 2. SkyPilot API server restarts, as long as the `~/.sky` folder is persisted
#    and the env var set during starting the server is the same.
SERVER_ID = common_utils.get_user_hash()


class ControllerType(enum.Enum):
    SERVE = 'SERVE'
    JOBS = 'JOBS'


def get_controller_name(controller_type: ControllerType) -> str:
    prefix = JOB_CONTROLLER_PREFIX
    if controller_type == ControllerType.SERVE:
        prefix = SKY_SERVE_CONTROLLER_PREFIX
    user_id = common_utils.get_user_hash()
    # Comparing the two IDs can determine if the caller is trying to get the
    # controller created by their local API server or a remote API server.
    if user_id == SERVER_ID:
        # Not adding server ID for locally created controller because
        # of backward compatibility.
        return f'{prefix}{user_id}'
    return f'{prefix}{user_id}{SERVER_ID_CONNECTOR}{SERVER_ID}'


# Controller names differ per user and per SkyPilot API server.
# If local: <prefix>-<user_id>
# If remote: <prefix>-<user_id>-remote-<api_server_user_id>
# DO NOT use these variables on the client side because client side doesn't know
# the remote server's user id, so client side will get local-version controller
# name.
# TODO(SKY-1106): remove dynamic constants like this.
SKY_SERVE_CONTROLLER_NAME: str = get_controller_name(ControllerType.SERVE)
JOB_CONTROLLER_NAME: str = get_controller_name(ControllerType.JOBS)


class StatusRefreshMode(enum.Enum):
    """The mode of refreshing the status of a cluster."""
    # Do not refresh any clusters.
    NONE = 'NONE'
    # Only refresh clusters if their autostop is set or have spot instances.
    AUTO = 'AUTO'
    # Refresh all clusters.
    FORCE = 'FORCE'


# Constants: minimize what target?
class OptimizeTarget(enum.Enum):
    COST = 0
    TIME = 1


def reload():
    # When a user request is sent to api server, it changes the user hash in the
    # env vars, but since controller_utils is imported before the env vars are
    # set, it doesn't get updated. So we need to reload it here.
    # pylint: disable=import-outside-toplevel
    from sky.utils import controller_utils
    global SKY_SERVE_CONTROLLER_NAME
    global JOB_CONTROLLER_NAME
    SKY_SERVE_CONTROLLER_NAME = get_controller_name(ControllerType.SERVE)
    JOB_CONTROLLER_NAME = get_controller_name(ControllerType.JOBS)
    importlib.reload(controller_utils)

    # We have to reload the kubernetes utils because it uses the lru_cache which
    # should be cleared between among requests.
    from sky.provision.kubernetes import utils
    importlib.reload(utils)

    # Make sure the logger takes the new environment variables. This is
    # necessary because the logger is initialized before the environment
    # variables are set, such as SKYPILOT_DEBUG.
    sky_logging.reload_logger()


def is_current_user_controller(controller_name: str) -> bool:
    """If the controller name belongs to the current user."""
    if SERVER_ID_CONNECTOR in controller_name:
        controller_name = controller_name.split(SERVER_ID_CONNECTOR)[0]
    controller_user_id = controller_name.split('-')[-1]
    return controller_user_id == common_utils.get_user_hash()

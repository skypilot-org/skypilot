"""Autostop utilities."""
import enum
import pickle
import shlex
import subprocess
import time
import typing
from typing import List, Optional

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.skylet import configs
from sky.skylet import constants
from sky.utils import message_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import psutil
else:
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger(__name__)

_AUTOSTOP_CONFIG_KEY = 'autostop_config'

# This key-value is stored inside the 'configs' sqlite3 database, because both
# user-issued commands (this module) and the Skylet process running the
# AutostopEvent need to access that state.
_AUTOSTOP_LAST_ACTIVE_TIME = 'autostop_last_active_time'
# AutostopEvent sets this to the boot time when the autostop of the cluster
# starts. This is used for checking whether the cluster is in the process
# of autostopping for the current machine.
_AUTOSTOP_INDICATOR = 'autostop_indicator'


class AutostopWaitFor(enum.Enum):
    """Enum for the Autostop behaviour.

    JOBS: Wait for jobs to finish.
    JOBS_AND_SSH: Wait for jobs to finish and all SSH sessions to be closed.
    NONE: Unconditionally stop the cluster after the idle time.
    """
    JOBS_AND_SSH = 'jobs_and_ssh'
    JOBS = 'jobs'
    NONE = 'none'

    @classmethod
    def supported_modes(cls) -> List[str]:
        return [mode.value for mode in cls]

    @classmethod
    def cli_help_message(cls, pair: str) -> str:
        return f"""\
Determines the condition for resetting the idleness timer.
This option works in conjunction with ``--{pair}``. Options:

\b
1. ``jobs_and_ssh`` (default): Wait for all jobs to complete AND all SSH
sessions to disconnect.
2. ``jobs``: Wait for all jobs to complete.
3. ``none``: Stop immediately after idle time expires, regardless of running
jobs or SSH connections."""

    @classmethod
    def from_str(cls, mode: str) -> 'AutostopWaitFor':
        """Returns the enum value for the given string."""
        if mode.lower() == cls.JOBS.value:
            return cls.JOBS
        elif mode.lower() == cls.JOBS_AND_SSH.value:
            return cls.JOBS_AND_SSH
        elif mode.lower() == cls.NONE.value:
            return cls.NONE
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unsupported autostop wait mode: '
                                 f'{mode}. The mode must be either '
                                 f'\'{cls.JOBS_AND_SSH.value}\', '
                                 f'\'{cls.JOBS.value}\', or '
                                 f'\'{cls.NONE.value}\'. ')


DEFAULT_AUTOSTOP_WAIT_FOR: AutostopWaitFor = AutostopWaitFor.JOBS_AND_SSH


class AutostopConfig:
    """Autostop configuration."""

    def __init__(self,
                 autostop_idle_minutes: int,
                 boot_time: float,
                 backend: Optional[str],
                 wait_for: AutostopWaitFor,
                 down: bool = False):
        assert autostop_idle_minutes < 0 or backend is not None, (
            autostop_idle_minutes, backend)
        self.autostop_idle_minutes = autostop_idle_minutes
        self.boot_time = boot_time
        self.backend = backend
        self.wait_for = wait_for
        self.down = down

    def __setstate__(self, state: dict):
        state.setdefault('down', False)
        self.__dict__.update(state)


def get_autostop_config() -> AutostopConfig:
    config_str = configs.get_config(_AUTOSTOP_CONFIG_KEY)
    if config_str is None:
        return AutostopConfig(-1, -1, None, DEFAULT_AUTOSTOP_WAIT_FOR)
    return pickle.loads(config_str)


def set_autostop(idle_minutes: int, backend: Optional[str],
                 wait_for: AutostopWaitFor, down: bool) -> None:
    boot_time = psutil.boot_time()
    autostop_config = AutostopConfig(idle_minutes, boot_time, backend, wait_for,
                                     down)
    configs.set_config(_AUTOSTOP_CONFIG_KEY, pickle.dumps(autostop_config))
    logger.debug(f'set_autostop(): idle_minutes {idle_minutes}, down {down}, '
                 f'wait_for {wait_for.value}.')
    # Reset timer whenever an autostop setting is submitted, i.e. the idle
    # time will be counted from now.
    set_last_active_time_to_now()


def set_autostopping_started() -> None:
    """Sets the boot time of the machine when autostop starts.

    This function should be called when the cluster is started to autostop,
    and the boot time of the machine will be stored in the configs database
    as an autostop indicator, which is used for checking whether the cluster
    is in the process of autostopping. The indicator is valid only when the
    machine has the same boot time as the one stored in the indicator.
    """
    logger.debug('Setting is_autostopping.')
    configs.set_config(_AUTOSTOP_INDICATOR, str(psutil.boot_time()))


def get_is_autostopping() -> bool:
    """Returns whether the cluster is in the process of autostopping."""
    result = configs.get_config(_AUTOSTOP_INDICATOR)
    is_autostopping = (result == str(psutil.boot_time()))
    return is_autostopping


def get_is_autostopping_payload() -> str:
    """Payload for whether the cluster is in the process of autostopping."""
    is_autostopping = get_is_autostopping()
    return message_utils.encode_payload(is_autostopping)


def get_last_active_time() -> float:
    """Returns the last active time, or -1 if none has been set."""
    result = configs.get_config(_AUTOSTOP_LAST_ACTIVE_TIME)
    if result is not None:
        return float(result)
    return -1


def set_last_active_time_to_now() -> None:
    """Sets the last active time to time.time()."""
    logger.debug('Setting last active time.')
    configs.set_config(_AUTOSTOP_LAST_ACTIVE_TIME, str(time.time()))


def has_active_ssh_sessions() -> bool:
    """Returns True if there are any active SSH sessions on the node."""
    try:
        # /dev/pts is a virtual filesystem that contains the pseudo-terminal
        # devices. ptmx is the pseudo-terminal multiplexer, which is the
        # "master" device that creates new pseudo-terminal devices, so we
        # exclude it from the count.
        proc = subprocess.run('ls /dev/pts | grep -v ptmx | wc -l',
                              capture_output=True,
                              text=True,
                              check=False,
                              shell=True)
        if proc.returncode != 0:
            logger.warning(f'SSH session check command failed with return code '
                           f'{proc.returncode}.')
            return False
        return int(proc.stdout.strip()) > 0
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Error checking active SSH sessions: {e}.')
        return False


class AutostopCodeGen:
    """Code generator for autostop utility functions.

    Usage:

      >> codegen = AutostopCodeGen.set_autostop(...)
    """
    _PREFIX = ['from sky.skylet import autostop_lib, constants']

    @classmethod
    def set_autostop(cls,
                     idle_minutes: int,
                     backend: str,
                     wait_for: Optional[AutostopWaitFor],
                     down: bool = False) -> str:
        if wait_for is None:
            wait_for = DEFAULT_AUTOSTOP_WAIT_FOR
        code = [
            f'\nif getattr(constants, "SKYLET_LIB_VERSION", 1) < 4: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, {down})'
            f'\nelse: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'autostop_lib.{wait_for}, {down})',
        ]
        return cls._build(code)

    @classmethod
    def is_autostopping(cls) -> str:
        code = ['print(autostop_lib.get_is_autostopping_payload())']
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'{constants.SKY_PYTHON_CMD} -u -c {shlex.quote(code)}'

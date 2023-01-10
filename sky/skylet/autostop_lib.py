"""Autostop utilities."""
import pickle
import psutil
import shlex
import time
from typing import List, Optional

from sky import sky_logging
from sky.skylet import configs

logger = sky_logging.init_logger(__name__)

_AUTOSTOP_CONFIG_KEY = 'autostop_config'

# This key-value is stored inside the 'configs' sqlite3 database, because both
# user-issued commands (this module) and the Skylet process running the
# AutostopEvent need to access that state.
_AUTOSTOP_LAST_ACTIVE_TIME = 'autostop_last_active_time'


class AutostopConfig:
    """Autostop configuration."""

    def __init__(self,
                 autostop_idle_minutes: int,
                 boot_time: int,
                 backend: Optional[str],
                 down: bool = False):
        assert autostop_idle_minutes < 0 or backend is not None, (
            autostop_idle_minutes, backend)
        self.autostop_idle_minutes = autostop_idle_minutes
        self.boot_time = boot_time
        self.backend = backend
        self.down = down

    def __setstate__(self, state: dict):
        state.setdefault('down', False)
        self.__dict__.update(state)


def get_autostop_config() -> AutostopConfig:
    config_str = configs.get_config(_AUTOSTOP_CONFIG_KEY)
    if config_str is None:
        return AutostopConfig(-1, -1, None)
    return pickle.loads(config_str)


def set_autostop(idle_minutes: int, backend: Optional[str], down: bool) -> None:
    boot_time = psutil.boot_time()
    autostop_config = AutostopConfig(idle_minutes, boot_time, backend, down)
    prev_autostop_config = get_autostop_config()
    configs.set_config(_AUTOSTOP_CONFIG_KEY, pickle.dumps(autostop_config))
    logger.debug(f'set_autostop(): idle_minutes {idle_minutes}, down {down}.')
    if (prev_autostop_config.autostop_idle_minutes < 0 or
            prev_autostop_config.boot_time != psutil.boot_time()):
        # Either autostop never set, or has been canceled. Reset timer.
        set_last_active_time_to_now()


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


class AutostopCodeGen:
    """Code generator for autostop utility functions.

    Usage:

      >> codegen = AutostopCodeGen.set_autostop(...)
    """
    _PREFIX = ['from sky.skylet import autostop_lib']

    @classmethod
    def set_autostop(cls, idle_minutes: int, backend: str, down: bool) -> str:
        code = [
            f'autostop_lib.set_autostop({idle_minutes}, {backend!r},'
            f' {down})',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'python3 -u -c {shlex.quote(code)}'

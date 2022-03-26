"""Sky autostop utility function."""
import json
import psutil
from typing import List, Optional

from sky.skylet import configs

_AUTOSTOP_CONFIG_KEY = 'autostop_config'


class AutostopConfig:

    def __init__(self, autostop_idle_minutes: int, boot_time: int,
                 backend: Optional[str]):
        assert autostop_idle_minutes < 0 or backend is not None, (
            autostop_idle_minutes, backend)
        self.autostop_idle_minutes = autostop_idle_minutes
        self.boot_time = boot_time
        self.backend = backend


def get_autostop_config() -> Optional[AutostopConfig]:
    config_str = configs.get_config(_AUTOSTOP_CONFIG_KEY)
    if config_str is None:
        return None
    return json.loads(config_str)


def set_autostop(idle_minutes: int, backend: Optional[str]) -> None:
    boot_time = psutil.boot_time()
    autostop_config = AutostopConfig(idle_minutes, boot_time, backend)
    configs.set_config(_AUTOSTOP_CONFIG_KEY, json.dumps(autostop_config))


class AutostopCodeGen:
    """Code generator for autostop utility functions.

    Usage:

      >> codegen = JobLibCodeGen.add_job(...)
    """
    _PREFIX = ['from sky.skylet import autostop_lib']

    @classmethod
    def set_autostop(cls, idle_minutes: int, backend: str) -> str:
        code = [
            f'autostop_lib.set_autostop({idle_minutes}, {backend!r})',
        ]
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'python3 -u -c {code!r}'

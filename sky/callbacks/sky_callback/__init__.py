from typing import Optional

from sky_callback.api import (
    init,
    on_step_begin,
    on_step_end,
    step,
    timer,
)
from sky_callback.base import BaseCallback


# FIXME(woosuk): Find a better way for lazy imports.
class CallbackLoader:

    @staticmethod
    def keras(log_dir: Optional[str] = None):
        from sky_callback.integrations.keras import SkyKerasCallback
        return SkyKerasCallback(log_dir=log_dir)

    @staticmethod
    def pytorch_lightning(log_dir: Optional[str] = None):
        from sky_callback.integrations.pytorch_lightning import SkyPLCallback
        return SkyPLCallback(log_dir=log_dir)

    @staticmethod
    def transformers(log_dir: Optional[str] = None):
        from sky_callback.integrations.transformers import SkyHFCallback
        return SkyHFCallback(log_dir=log_dir)


SkyKerasCallback = CallbackLoader.keras
SkyPLCallback = CallbackLoader.pytorch_lightning
SkyHFCallback = CallbackLoader.transformers

__all__ = [
    # APIs
    'init',
    'on_step_begin',
    'on_step_end',
    'step',
    'timer',
    # Callbacks
    'BaseCallback',
    'SkyKerasCallback',
    'SkyPLCallback',
    'SkyHFCallback',
]

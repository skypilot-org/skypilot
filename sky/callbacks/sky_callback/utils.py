import os
from typing import Optional

DISABLE_CALLBACK = os.environ.get('SKY_DISABLE_CALLBACK',
                                  'False').lower() in ('true', '1')


# FIXME(woosuk): Find a better way for lazy imports.
class CallbackLoader:

    @staticmethod
    def keras(log_dir: Optional[str] = None, total_steps: Optional[int] = None):
        from sky_callback.integrations.keras import SkyKerasCallback
        return SkyKerasCallback(log_dir=log_dir, total_steps=total_steps)

    @staticmethod
    def pytorch_lightning(log_dir: Optional[str] = None, total_steps: Optional[int] = None):
        from sky_callback.integrations.pytorch_lightning import SkyLightningCallback
        return SkyLightningCallback(log_dir=log_dir, total_steps=total_steps)

    @staticmethod
    def transformers(log_dir: Optional[str] = None, total_steps: Optional[int] = None):
        from sky_callback.integrations.transformers import SkyTransformersCallback
        return SkyTransformersCallback(log_dir=log_dir, total_steps=total_steps)

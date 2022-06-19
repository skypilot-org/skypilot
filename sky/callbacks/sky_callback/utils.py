import os
from typing import Optional

DISABLE_CALLBACK = os.environ.get('SKY_DISABLE_CALLBACK',
                                  'False').lower() in ('true', '1')

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

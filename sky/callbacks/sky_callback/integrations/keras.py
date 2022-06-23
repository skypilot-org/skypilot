"""SkyCallback integration with Keras."""
from tensorflow import keras

from sky_callback import base
from sky_callback import utils

DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyKerasCallback(keras.callbacks.Callback):
    """SkyCallback for Keras.

    Example:
        ```python
        from sky_callback import SkyKerasCallback
        model.fit(..., callbacks=[SkyKerasCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
    """

    def __init__(self, log_dir=None, total_steps=None):
        self.log_dir = log_dir
        self.total_steps = total_steps
        self.sky_callback = None

    def on_train_begin(self, logs=None):
        if DISABLE_CALLBACK:
            return
        # TODO(woosuk): Add support for distributed training.
        assert self.sky_callback is None
        self.sky_callback = base.BaseCallback(log_dir=self.log_dir)

    def on_train_batch_begin(self, batch, logs=None):
        if DISABLE_CALLBACK:
            return
        self.sky_callback.on_step_begin()

    def on_train_batch_end(self, batch, logs=None):
        if DISABLE_CALLBACK:
            return
        self.sky_callback.on_step_end()

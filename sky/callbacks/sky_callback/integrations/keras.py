from tensorflow import keras

from sky_callback import base


class SkyCallback(keras.callbacks.Callback):

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def _lazy_init(self):
        self.sky_callback = base.BaseCallback(self.log_dir)

    def on_train_begin(self, logs=None):
        if self.sky_callback is None:
            self._lazy_init()
        self.sky_callback.config()

    def on_train_batch_begin(self, batch, logs=None):
        self.sky_callback.on_step_begin()

    def on_train_batch_end(self, batch, logs=None):
        self.sky_callback.on_step_end()

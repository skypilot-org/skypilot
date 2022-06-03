from tensorflow import keras

from sky_callback import base


class SkyCallback(keras.callbacks.Callback):

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def _lazy_init(self):
        if self.log_dir is None:
            self.sky_callback = base.SkyCallback()
        else:
            self.sky_callback = base.SkyCallback(log_dir=self.log_dir)

    def on_train_begin(self, logs=None):
        self.sky_callback.config()

    def on_train_batch_begin(self, batch, logs=None):
        self.sky_callback.on_train_step_begin()

    def on_train_batch_end(self, batch, logs=None):
        self.sky_callback.on_train_step_end()

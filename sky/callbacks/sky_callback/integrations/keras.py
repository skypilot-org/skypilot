from tensorflow import keras

from sky_callback import base


class SkyKerasCallback(keras.callbacks.Callback):

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def on_train_begin(self, logs=None):
        # TODO(woosuk): Add support for distributed training.
        if self.sky_callback is None:
            self.sky_callback = base.BaseCallback(log_dir=self.log_dir)

    def on_train_batch_begin(self, batch, logs=None):
        self.sky_callback.on_step_begin()

    def on_train_batch_end(self, batch, logs=None):
        self.sky_callback.on_step_end()

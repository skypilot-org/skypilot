import tensorflow as tf
from tensorflow import keras

from sky.skylet.callback import base


class SkyCallback(keras.callbacks.Callback):

    def __init__(self, log_dir=None):
        if log_dir is None:
            self.sky_callback = base.SkyCallback()
        else:
            self.sky_callback = base.SkyCallback(log_dir=log_dir)

    def on_train_begin(self, logs=None):
        self.sky_callback.save_timestamp()

    def on_train_batch_end(self, batch, logs=None):
        self.sky_callback.save_timestamp()

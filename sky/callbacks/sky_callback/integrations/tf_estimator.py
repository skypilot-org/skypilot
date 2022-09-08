import tensorflow.compat.v1 as tf1

from sky_callback import base


class SkyTFEstimatorCallback(tf1.train.SessionRunHook):

    def __init__(self, log_dir=None, total_steps=None):
        super().__init__()
        self._log_dir = log_dir
        self._total_steps = total_steps
        self._sky_callback = None

    def begin(self):
        self._sky_callback = base.BaseCallback(log_dir=self._log_dir,
                                               total_steps=self._total_steps)

    def before_run(self, run_context):
        self._sky_callback.on_step_begin()

    def after_run(self, run_context, run_values):
        self._sky_callback.on_step_end()

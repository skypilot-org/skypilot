import transformers

from sky_callback import base


class SkyCallback(transformers.TrainerCallback):

    def __init__(self, log_dir=None):
        if log_dir is None:
            self.sky_callback = base.SkyCallback()
        else:
            self.sky_callback = base.SkyCallback(log_dir=log_dir)

    def on_train_begin(self, args, state, control, **kwargs):
        self.sky_callback.save_timestamp()

    def on_step_end(self, args, state, control, **kwargs):
        self.sky_callback.save_timestamp()

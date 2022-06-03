import transformers

from sky_callback import base


class SkyCallback(transformers.TrainerCallback):

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def _lazy_init(self):
        if self.log_dir is None:
            self.sky_callback = base.SkyCallback()
        else:
            self.sky_callback = base.SkyCallback(log_dir=self.log_dir)

    def on_train_begin(self, args, state, control, **kwargs):
        if state.is_world_process_zero:
            if self.sky_callback is None:
                self._lazy_init()
            self.sky_callback.config(total_train_steps=state.max_steps)

    def on_step_begin(self, args, state, control, **kwargs):
        if self.sky_callback is not None:
            self.sky_callback.on_train_step_begin()

    def on_step_end(self, args, state, control, **kwargs):
        if self.sky_callback is not None:
            self.sky_callback.on_train_step_begin()

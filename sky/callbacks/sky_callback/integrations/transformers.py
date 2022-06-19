"""SkyCallback integration with HuggingFace Transformers."""
import transformers

from sky_callback import base


class SkyHFCallback(transformers.TrainerCallback):
    """SkyCallback for HuggingFace Transformers.
    
    Example:
        ```python
        from sky_callback import SkyHFCallback
        trainer = transformers.Trainer(..., callbacks=[SkyHFCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
    """

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def on_train_begin(self, args, state, control, **kwargs):
        if state.is_world_process_zero:
            assert self.sky_callback is None
            self.sky_callback = base.BaseCallback(log_dir=self.log_dir,
                                                  total_steps=state.max_steps)

    def on_step_begin(self, args, state, control, **kwargs):
        if self.sky_callback is not None:
            self.sky_callback.on_step_begin()

    def on_step_end(self, args, state, control, **kwargs):
        if self.sky_callback is not None:
            self.sky_callback.on_step_end()

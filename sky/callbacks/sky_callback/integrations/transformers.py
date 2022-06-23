"""SkyCallback integration with HuggingFace Transformers."""
import transformers

from sky_callback import base
from sky_callback import utils

DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyTransformersCallback(transformers.TrainerCallback):
    """SkyCallback for HuggingFace Transformers.
    
    Example:
        ```python
        from sky_callback import SkyTransformersCallback
        trainer = transformers.Trainer(..., callbacks=[SkyTransformersCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
        total_steps: A total number of steps. If None, it is inferred from
            the trainer state.
    """

    def __init__(self, log_dir=None, total_steps=None):
        self.log_dir = log_dir
        self.total_steps = total_steps
        self.sky_callback = None

    def _infer_total_steps(self, state):
        if self.total_steps is not None:
            return self.total_steps
        return state.max_steps

    def on_train_begin(self, args, state, control, **kwargs):
        del args, control, kwargs  # Unused.
        if DISABLE_CALLBACK:
            return
        assert self.sky_callback is None
        if state.is_world_process_zero:
            total_steps = self._infer_total_steps(state)
            self.sky_callback = base.BaseCallback(log_dir=self.log_dir,
                                                  total_steps=total_steps)

    def on_step_begin(self, args, state, control, **kwargs):
        del args, state, control, kwargs  # Unused.
        if DISABLE_CALLBACK:
            return
        if self.sky_callback is not None:
            self.sky_callback.on_step_begin()

    def on_step_end(self, args, state, control, **kwargs):
        del args, state, control, kwargs  # Unused.
        if DISABLE_CALLBACK:
            return
        if self.sky_callback is not None:
            self.sky_callback.on_step_end()

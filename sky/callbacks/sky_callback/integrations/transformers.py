"""SkyCallback integration with HuggingFace Transformers."""
from typing import Optional

import transformers

from sky_callback import base
from sky_callback import utils

_DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyTransformersCallback(transformers.TrainerCallback):
    """SkyCallback for HuggingFace Transformers.

    Refer to: https://huggingface.co/docs/transformers/main_classes/callback

    Example:
        ```python
        from sky_callback import SkyTransformersCallback
        trainer = transformers.Trainer(..., callbacks=[SkyTransformersCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
        total_steps: The total number of steps. If None, it is inferred from
            the trainer state.
    """

    def __init__(self,
                 log_dir: Optional[str] = None,
                 total_steps: Optional[int] = None) -> None:
        self._log_dir = log_dir
        self._total_steps = total_steps
        self._sky_callback = None

    def _infer_total_steps(self, args: transformers.TrainingArguments,
                           state: transformers.TrainerState) -> Optional[int]:
        """Infer the total number of steps from the trainer state."""
        if self._total_steps is not None:
            return self._total_steps
        total_steps = state.max_steps
        if total_steps is None or total_steps < 0:
            return None
        return total_steps

    def on_train_begin(self, args: transformers.TrainingArguments,
                       state: transformers.TrainerState,
                       control: transformers.TrainerControl, **kwargs) -> None:
        del control, kwargs  # Unused.
        if _DISABLE_CALLBACK:
            return
        assert self._sky_callback is None
        if state.is_world_process_zero:
            total_steps = self._infer_total_steps(args, state)
            self._sky_callback = base.BaseCallback(log_dir=self._log_dir,
                                                   total_steps=total_steps)

    def on_step_begin(self, args: transformers.TrainingArguments,
                      state: transformers.TrainerState,
                      control: transformers.TrainerControl, **kwargs) -> None:
        del args, state, control, kwargs  # Unused.
        if _DISABLE_CALLBACK:
            return
        if self._sky_callback is not None:
            self._sky_callback.on_step_begin()

    def on_substep_end(self, args: transformers.TrainingArguments,
                       state: transformers.TrainerState,
                       control: transformers.TrainerControl, **kwargs) -> None:
        del args, state, control, kwargs  # Unused.
        if _DISABLE_CALLBACK:
            return
        if self._sky_callback is not None:
            self._sky_callback.on_step_end()

    def on_step_end(self, args: transformers.TrainingArguments,
                    state: transformers.TrainerState,
                    control: transformers.TrainerControl, **kwargs) -> None:
        del args, state, control, kwargs  # Unused.
        if _DISABLE_CALLBACK:
            return
        if self._sky_callback is not None:
            self._sky_callback.on_step_end()

"""SkyCallback integration with Keras."""
from typing import Dict, Optional

from tensorflow import keras

from sky_callback import base
from sky_callback import utils

DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyKerasCallback(keras.callbacks.Callback):
    """SkyCallback for Keras.

    Example:
        ```python
        from sky_callback import SkyKerasCallback
        model.fit(..., callbacks=[SkyKerasCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
        total_steps: A total number of steps. If None, it is inferred from
            the parameters passed in model.fit().
    """

    def __init__(self,
                 log_dir: Optional[str] = None,
                 total_steps: Optional[int] = None) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.total_steps = total_steps
        self._sky_callback = None

    def _infer_total_steps(self) -> Optional[int]:
        if self.total_steps is not None:
            return self.total_steps

        epochs = self.params['epochs']
        steps_per_epoch = self.params['steps']
        if steps_per_epoch is None:
            total_steps = None
        else:
            total_steps = epochs * steps_per_epoch
        return total_steps

    def on_train_begin(self, logs: Dict = None) -> None:
        del logs  # Unused.
        if DISABLE_CALLBACK:
            return
        assert self._sky_callback is None
        # TODO(woosuk): Add support for distributed training.
        total_steps = self._infer_total_steps()
        self._sky_callback = base.BaseCallback(log_dir=self.log_dir,
                                              total_steps=total_steps)

    def on_train_batch_begin(self, batch: int, logs: Dict = None) -> None:
        del batch, logs  # Unused.
        if DISABLE_CALLBACK:
            return
        self._sky_callback.on_step_begin()

    def on_train_batch_end(self, batch: int, logs: Dict = None) -> None:
        del batch, logs  # Unused.
        if DISABLE_CALLBACK:
            return
        self._sky_callback.on_step_end()

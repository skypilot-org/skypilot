"""SkyCallback integration with Keras."""
from typing import Dict, Optional

import tensorflow as tf
from tensorflow import keras

from sky_callback import base
from sky_callback import utils

_DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyKerasCallback(keras.callbacks.Callback):
    """SkyCallback for Keras.

    Refer to: https://keras.io/api/callbacks/

    Example:
        ```python
        from sky_callback import SkyKerasCallback
        model.fit(..., callbacks=[SkyKerasCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
        total_steps: The total number of steps. If None, it is inferred from
            the parameters passed in model.fit().
    """

    def __init__(self,
                 log_dir: Optional[str] = None,
                 total_steps: Optional[int] = None) -> None:
        super().__init__()
        self._log_dir = log_dir
        self._total_steps = total_steps
        self._sky_callback = None

    def _is_chief(self) -> bool:
        strategy = self.model.distribute_strategy
        if strategy is None:
            strategy = tf.distribute.get_strategy()

        if strategy is None:
            # Not in distributed training.
            return True
        if not strategy.extended._in_multi_worker_mode():
            # Not in multi-worker distributed training.
            return True
        if strategy.extended.should_checkpoint:
            # Chief worker.
            return True
        return False

    def _infer_total_steps(self) -> Optional[int]:
        if self._total_steps is not None:
            return self._total_steps

        epochs = self.params['epochs']
        steps_per_epoch = self.params['steps']
        if steps_per_epoch is None:
            total_steps = None
        else:
            total_steps = epochs * steps_per_epoch
        return total_steps

    def on_train_begin(self, logs: Dict = None) -> None:
        del logs  # Unused.
        if _DISABLE_CALLBACK:
            return
        assert self._sky_callback is None
        if self._is_chief():
            total_steps = self._infer_total_steps()
            self._sky_callback = base.BaseCallback(log_dir=self._log_dir,
                                                   total_steps=total_steps)

    def on_train_batch_begin(self, batch: int, logs: Dict = None) -> None:
        del batch, logs  # Unused.
        if _DISABLE_CALLBACK:
            return
        self._sky_callback.on_step_begin()

    def on_train_batch_end(self, batch: int, logs: Dict = None) -> None:
        del batch, logs  # Unused.
        if _DISABLE_CALLBACK:
            return
        self._sky_callback.on_step_end()

"""SkyCallback integration with PyTorch Lightning."""
import pytorch_lightning as pl

from sky_callback import base
from sky_callback import utils

DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyPLCallback(pl.Callback):
    """SkyCallback for PyTorch Lightning.
    
    Example:
        ```python
        from sky_callback import SkyPLCallback
        trainer = pl.Trainer(..., callbacks=[SkyPLCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
    """

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def on_train_start(self, trainer, pl_module):
        if DISABLE_CALLBACK:
            return
        assert self.sky_callback is None
        if trainer.global_rank == 0:
            max_epochs = trainer.max_epochs
            max_steps = trainer.max_steps
            # This is safe because `on_train_start` is always called after
            # `reset_train_dataloader` which sets `num_trainig_batches`.
            num_training_batches = trainer.num_training_batches

            if max_epochs == -1 and max_steps == -1:
                # Infinite training.
                total_steps = None
            elif num_training_batches == float('inf'):
                # Iterable dataset. `total_steps` is known only if
                # `max_steps` is set.
                total_steps = max_steps if max_steps != -1 else None
            else:
                total_steps = num_training_batches * max(max_epochs, 1)
                if max_steps != -1:
                    total_steps = min(total_steps, max_steps)
            # FIXME(woosuk): Check the early stopping flag.
            # If it is set, total_steps should be None.
            self.sky_callback = base.BaseCallback(log_dir=self.log_dir,
                                                  total_steps=total_steps)

    def on_train_batch_start(
        self,
        trainer,
        pl_module,
        batch,
        batch_idx,
    ):
        if DISABLE_CALLBACK:
            return
        if self.sky_callback is not None:
            self.sky_callback.on_step_begin()

    def on_train_batch_end(
        self,
        trainer,
        pl_module,
        outputs,
        batch,
        batch_idx,
    ):
        if DISABLE_CALLBACK:
            return
        if self.sky_callback is not None:
            self.sky_callback.on_step_end()

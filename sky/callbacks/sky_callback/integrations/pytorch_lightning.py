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
            self.sky_callback = base.BaseCallback(log_dir=self.log_dir,
                                                  total_steps=trainer.max_steps)

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

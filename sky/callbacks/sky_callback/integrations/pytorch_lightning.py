import pytorch_lightning as pl

from sky_callback import base


class SkyPLCallback(pl.Callback):

    def __init__(self, log_dir=None):
        self.log_dir = log_dir
        self.sky_callback = None

    def on_train_start(self, trainer, pl_module):
        if trainer.global_rank == 0:
            if self.sky_callback is None:
                self.sky_callback = base.BaseCallback(log_dir=self.log_dir, total_steps=trainer.max_steps)

    def on_train_batch_start(
        self,
        trainer,
        pl_module,
        batch,
        batch_idx,
    ):
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
        if self.sky_callback is not None:
            self.sky_callback.on_step_end()

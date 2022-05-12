import pytorch_lightning as pl

from sky.skylet.callback import base


class SkyCallback(pl.Callback):

    def __init__(self, log_dir=None):
        super().__init__()
        if log_dir is None:
            self.sky_callback = base.SkyCallback()
        else:
            self.sky_callback = base.SkyCallback(log_dir=log_dir)

    def on_train_start(self, trainer, pl_module):
        self.sky_callback.save_timestamp()

    def on_train_batch_end(
        self,
        trainer,
        pl_module,
        outputs,
        batch,
        batch_idx,
        unused,
    ):
        self.sky_callback.save_timestamp()

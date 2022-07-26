"""SkyCallback integration with PyTorch Lightning."""
from typing import Any, Optional

import pytorch_lightning as pl

from sky_callback import base
from sky_callback import utils

_DISABLE_CALLBACK = utils.DISABLE_CALLBACK


class SkyLightningCallback(pl.Callback):
    """SkyCallback for PyTorch Lightning.

    Refer to: https://pytorch-lightning.readthedocs.io/en/latest/extensions/callbacks.html  # pylint: disable=line-too-long

    Example:
        ```python
        from sky_callback import SkyLightningCallback
        trainer = pl.Trainer(..., callbacks=[SkyLightningCallback()])
        ```

    Args:
        log_dir: A directory to store the logs.
        total_steps: The total number of steps. If None, it is inferred from
            the trainer.
    """

    def __init__(self,
                 log_dir: Optional[str] = None,
                 total_steps: Optional[int] = None) -> None:
        self._log_dir = log_dir
        self._total_steps = total_steps
        self._sky_callback = None

    def _infer_total_steps(self, trainer: pl.Trainer) -> Optional[int]:
        if self._total_steps is not None:
            return self._total_steps

        total_steps = trainer.estimated_stepping_batches
        if total_steps == float('inf') or total_steps < 0:
            return None
        return total_steps

    def on_train_start(self, trainer: pl.Trainer,
                       pl_module: pl.LightningModule) -> None:
        del pl_module  # Unused.
        if _DISABLE_CALLBACK:
            return
        assert self._sky_callback is None
        if trainer.global_rank == 0:
            total_steps = self._infer_total_steps(trainer)
            self._sky_callback = base.BaseCallback(log_dir=self._log_dir,
                                                   total_steps=total_steps)

    def on_train_batch_start(
        self,
        trainer: pl.Trainer,
        pl_module: pl.LightningModule,
        batch: Any,
        batch_idx: int,
    ) -> None:
        del trainer, pl_module, batch, batch_idx  # Unused.
        if _DISABLE_CALLBACK:
            return
        if self._sky_callback is not None:
            self._sky_callback.on_step_begin()

    def on_train_batch_end(
        self,
        trainer: pl.Trainer,
        pl_module: pl.LightningModule,
        outputs: Any,
        batch: Any,
        batch_idx: int,
    ) -> None:
        del trainer, pl_module, outputs, batch, batch_idx  # Unused.
        if _DISABLE_CALLBACK:
            return
        if self._sky_callback is not None:
            self._sky_callback.on_step_end()

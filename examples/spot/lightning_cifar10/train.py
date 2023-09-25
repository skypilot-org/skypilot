# Code modified from https://pytorch-lightning.readthedocs.io/en/stable/notebooks/lightning_examples/cifar10-baseline.html

import argparse
import glob
import os

from pl_bolts.datamodules import CIFAR10DataModule
from pl_bolts.transforms.dataset_normalizations import cifar10_normalization
from pytorch_lightning import LightningModule
from pytorch_lightning import seed_everything
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import LearningRateMonitor
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.loggers import WandbLogger
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.optim.lr_scheduler import OneCycleLR
from torch.optim.swa_utils import AveragedModel
from torch.optim.swa_utils import update_bn
from torchmetrics.functional import accuracy
import torchvision

seed_everything(7)

PATH_DATASETS = os.environ.get("PATH_DATASETS", ".")
AVAIL_GPUS = min(1, torch.cuda.device_count())
BATCH_SIZE = 256 if AVAIL_GPUS else 64
NUM_WORKERS = int(os.cpu_count() / 2)

train_transforms = torchvision.transforms.Compose([
    torchvision.transforms.RandomCrop(32, padding=4),
    torchvision.transforms.RandomHorizontalFlip(),
    torchvision.transforms.ToTensor(),
    cifar10_normalization(),
])

test_transforms = torchvision.transforms.Compose([
    torchvision.transforms.ToTensor(),
    cifar10_normalization(),
])

cifar10_dm = CIFAR10DataModule(
    data_dir=PATH_DATASETS,
    batch_size=BATCH_SIZE,
    num_workers=NUM_WORKERS,
    train_transforms=train_transforms,
    test_transforms=test_transforms,
    val_transforms=test_transforms,
)


def create_model():
    model = torchvision.models.resnet18(pretrained=False, num_classes=10)
    model.conv1 = nn.Conv2d(3,
                            64,
                            kernel_size=(3, 3),
                            stride=(1, 1),
                            padding=(1, 1),
                            bias=False)
    model.maxpool = nn.Identity()
    return model


class LitResnet(LightningModule):

    def __init__(self, lr=0.05):
        super().__init__()

        self.save_hyperparameters()
        self.model = create_model()

    def forward(self, x):
        out = self.model(x)
        return F.log_softmax(out, dim=1)

    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)
        self.log("train_loss", loss)
        return loss

    def evaluate(self, batch, stage=None):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)
        preds = torch.argmax(logits, dim=1)
        acc = accuracy(preds, y)

        if stage:
            self.log(f"{stage}_loss", loss, prog_bar=True)
            self.log(f"{stage}_acc", acc, prog_bar=True)

    def validation_step(self, batch, batch_idx):
        self.evaluate(batch, "val")

    def test_step(self, batch, batch_idx):
        self.evaluate(batch, "test")

    def configure_optimizers(self):
        optimizer = torch.optim.SGD(
            self.parameters(),
            lr=self.hparams.lr,
            momentum=0.9,
            weight_decay=5e-4,
        )
        steps_per_epoch = 45000 // BATCH_SIZE
        scheduler_dict = {
            "scheduler": OneCycleLR(
                optimizer,
                0.1,
                epochs=self.trainer.max_epochs,
                steps_per_epoch=steps_per_epoch,
            ),
            "interval": "step",
        }
        return {"optimizer": optimizer, "lr_scheduler": scheduler_dict}


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--num_epochs",
                        type=int,
                        help="Number of training epochs.",
                        default=30)
    parser.add_argument("--checkpoint_per_n_steps",
                        type=int,
                        help="Checkpoint every N training steps.",
                        default=500)
    parser.add_argument("--resume",
                        action="store_true",
                        help="Resume training from saved checkpoint.")
    parser.add_argument("--run_id", type=str, help="W&B run id.", default=None)
    parser.add_argument("--root_dir",
                        type=str,
                        help="Directory for saving checkpoints.",
                        required=True)

    argv = parser.parse_args()

    model = LitResnet(lr=0.05)
    model.datamodule = cifar10_dm

    checkpoint_callback = ModelCheckpoint(
        dirpath=argv.root_dir,
        save_top_k=3,
        monitor="step",
        every_n_train_steps=argv.checkpoint_per_n_steps,
        save_last=True)

    trainer = Trainer(
        progress_bar_refresh_rate=10,
        max_epochs=argv.num_epochs,
        gpus=AVAIL_GPUS,
        logger=WandbLogger(project="cifar-lit", id=argv.run_id,
                           save_dir="/tmp"),
        callbacks=[
            LearningRateMonitor(logging_interval="step"), checkpoint_callback
        ],
        default_root_dir=argv.root_dir,
    )

    model_ckpts = glob.glob(argv.root_dir + "/*.ckpt")
    if argv.resume and len(model_ckpts) > 0:
        latest_ckpt = max(model_ckpts, key=os.path.getctime)
        trainer.fit(model, cifar10_dm, ckpt_path=latest_ckpt)
    else:
        trainer.fit(model, cifar10_dm)
    trainer.test(model, datamodule=cifar10_dm)


if __name__ == "__main__":
    main()

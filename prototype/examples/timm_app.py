"""Pytorch Image Models (the timm package).

TODO: timm doesn't take in gs:// (fake imagenet data hosted by Google), so this
example finishes up to provisioning and doesn't really start training.  An
error on data path being invalid is expected.  We should investigate supporting
large datasets.
"""
import os
import subprocess

import sky

PROJECT_DIR = '~/Downloads/pytorch-image-models'


def clone_project():
    if not os.path.isdir(os.path.expanduser(PROJECT_DIR)):
        subprocess.run(
            'git clone https://github.com/rwightman/pytorch-image-models {}'.
            format(PROJECT_DIR),
            shell=True,
            check=True)


clone_project()

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = PROJECT_DIR

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install timm pyyaml'

    # The command to run.  Will be run under the working directory.
    # https://rwightman.github.io/pytorch-image-models/training_hparam_examples/

    # fake_imagenet (tfrecords) doesn't work:
    #  RuntimeError: Found 0 images in subfolders of
    #  /tmp/fake_imagenet. Supported image extensions are .png, .jpg, .jpeg
    run = './distributed_train.sh 1 /tmp/fake_imagenet --model efficientnet_b2 -b 128 --sched step --epochs 450 --decay-epochs 2.4 --decay-rate .97 --opt rmsproptf --opt-eps .001 -j 8 --warmup-lr 1e-6 --weight-decay 1e-5 --drop 0.3 --drop-connect 0.2 --model-ema --model-ema-decay 0.9999 --aa rand-m9-mstd0.5 --remode pixel --reprob 0.2 --amp --lr .016'

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_file_mounts({
        # Download from GCS.
        '/tmp/fake_imagenet/': 'gs://cloud-tpu-test-datasets/fake_imagenet/',
    })
    train.set_resources({sky.Resources(sky.AWS(), accelerators='V100')})

dag = sky.optimize(dag)
sky.execute(dag)

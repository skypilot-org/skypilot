import sky
from sky import clouds

import time_estimators

##############################
# Options for inputs:
#
#  P0:
#    - read from the specified cloud store, continuously
#    - sync submission-site local files to remote FS
#
#  1. egress from the specified cloud store, to local
#
#  TODO: if egress, what bucket to use for the destination cloud store?
#
##############################
# Options for outputs:
#
#  P0. write to local only (don't destroy VM at the end)
#
#  P1. write to local, sync to a specified cloud's bucket
#
#  P2. continuously write checkpoints to a specified cloud's bucket
#       TODO: this is data egress from run_cloud; not taken into account

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '~/Downloads/tpu'

    # The setup command.  Will be run under the working directory.
    setup = 'pip install --upgrade pip && \
        conda activate resnet || \
          (conda create -n resnet python=3.7 -y && \
           conda activate resnet && \
           pip install tensorflow==2.4.0 pyyaml && \
           cd models && pip install -e .)'

    # The command to run.  Will be run under the working directory.
    run = 'conda activate resnet && \
        python -u models/official/resnet/resnet_main.py --use_tpu=False \
        --mode=train --train_batch_size=256 --train_steps=250 \
        --iterations_per_loop=125 \
        --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet \
        --model_dir=resnet-model-dir \
        --amp --xla --loss_scale=128'

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        # sky.Resources(clouds.AWS(), 'p3.2xlarge'),
        # sky.Resources(clouds.GCP(), 'n1-standard-16'),
        sky.Resources(
            clouds.GCP(),
            # Format for GPUs: '<num>x <name>', or '<name>' (implies num=1).
            # Examples: 'V100', '1x P100', '4x V100'.
            ('n1-standard-8', '1x V100'),
        )
    })
    train.set_estimate_runtime_func(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)

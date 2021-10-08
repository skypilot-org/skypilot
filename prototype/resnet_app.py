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
    working_dir = '/Users/zongheng/Dropbox/workspace/riselab/tpu'

    # The setup command.  Will be run under the working directory.
    setup = 'pip install tensorflow==2.4.0 pyyaml && cd models && pip install -e .'

    # The run command.  Will be run under the working directory.
    run = 'python models/official/resnet/resnet_main.py --use_tpu=False --mode=train \
    --train_batch_size=256 --train_steps=250 --iterations_per_loop=125 \
    --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet \
    --model_dir=resnet-model-dir --amp --xla --loss_scale=128'

    train = sky.Task(
        'train',
        command=run,
        setup_command=setup,
        working_dir=working_dir,
    )
    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
        # sky.Resources(clouds.GCP(), ('1x V100', 'n1-standard-4')),
    })
    train.set_estimate_runtime_func(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
sky.execute(dag, teardown=False)

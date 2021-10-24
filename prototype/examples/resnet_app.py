import sky
from sky import clouds

import time_estimators

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
    # TODO: allow option to say (or detect) no download/egress cost.
    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        ##### Fully specified
        # sky.Resources(clouds.AWS(), 'p3.2xlarge'),
        # sky.Resources(clouds.GCP(), 'n1-standard-16'),
        # sky.Resources(
        #     clouds.GCP(),
        #     'n1-standard-8',
        #     # Options: 'V100', {'V100': <num>}.
        #     'V100',
        # ),
        ##### Partially specified
        sky.Resources(accelerators='V100'),
        # sky.Resources(accelerators='tpu-v3-8'),
        # sky.Resources(clouds.AWS(), accelerators={'V100': 4}),
        # sky.Resources(clouds.AWS(), accelerators='V100'),
    })

    # Optionally, specify a time estimator: Resources -> time in seconds.
    # train.set_time_estimator(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)

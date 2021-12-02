import subprocess

import sky
from sky import clouds, Storage

import time_estimators

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '~/Downloads/tpu'
    data_mount_path = '/tmp/imagenet'
    subprocess.run(f'cd {workdir} && git checkout 222cc86',
                   shell=True,
                   check=True)

    # The setup command.  Will be run under the working directory.
    setup = 'echo \"alias python=python3\" >> ~/.bashrc && \
        echo \"alias pip3=pip\" >> ~/.bashrc && \
        source ~/.bashrc && \
        pip install --upgrade pip && \
        pip install awscli botocore boto3 && \
        conda init bash && \
        conda activate resnet || \
          (conda create -n resnet python=3.7 -y && \
           conda activate resnet && \
           pip install tensorflow==2.4.0 pyyaml && \
           cd models && pip install -e .)'

    # The command to run.  Will be run under the working directory.
    run = f'conda activate resnet && \
        python -u models/official/resnet/resnet_main.py --use_tpu=False \
        --mode=train --train_batch_size=256 --train_steps=250 \
        --iterations_per_loop=125 \
        --data_dir={data_mount_path} \
        --model_dir=resnet-model-dir \
        --amp --xla --loss_scale=128'

    storage = Storage(name="imagenet-bucket",
                      source_path="s3://imagenet-bucket",
                      default_mount_path=data_mount_path)
    train = sky.Task(
        'train',
        workdir=workdir,
        storage=storage,
        setup=setup,
        run=run,
    )
    # File mount != Data mount, File mount is slow and is for direc
    train.set_file_mounts({})
    # TODO: allow option to say (or detect) no download/egress cost.
    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        ##### Fully specified
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
    })

    # Optionally, specify a time estimator: Resources -> time in seconds.
    # train.set_time_estimator(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)

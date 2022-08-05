import subprocess

import sky

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
           conda install cudatoolkit=11.0 -y && \
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

    # If the backend to be added is not specified, then SkyPilot's optimizer
    # will choose the backend bucket to be stored.
    # S3 Example
    storage = sky.Storage(name="imagenet-bucket", source="s3://imagenet-bucket")
    # GCS Example
    #storage = sky.Storage(name="imagenet_test_mluo",source="gs://imagenet_test_mluo")
    # Can also be from a local dir
    # storage = sky.Storage(name="imagenet-bucket", source="~/imagenet-data/")

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_storage_mounts({
        data_mount_path: storage,
    })

    train.set_inputs('s3://imagenet-bucket', estimated_size_gigabytes=150)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        sky.Resources(sky.AWS(), 'p3.2xlarge'),
    })

sky.launch(dag)

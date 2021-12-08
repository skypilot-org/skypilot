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

    # If the backend to be added is not specified, then Sky optimizer will
    # choose the backend bucket to be stored.
    storage = sky.Storage(name="imagenet-bucket",
                          source="s3://imagenet-bucket",
                          default_mount_path=data_mount_path)
    train = sky.Task(
        'train',
        workdir=workdir,
        storage=storage,
        setup=setup,
        run=run,
    )
    train.set_inputs('s3://imagenet-bucket', estimated_size_gigabytes=150)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        ##### Fully specified
        sky.Resources(sky.AWS(), 'p3.2xlarge'),
    })

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)

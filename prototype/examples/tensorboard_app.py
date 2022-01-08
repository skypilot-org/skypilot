import subprocess

import sky

# Start the instance.
with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '~/Downloads/tpu'
    subprocess.run(f'cd {workdir} && git checkout 222cc86',
                   shell=True,
                   check=True)
    # The setup command.  Will be run under the working directory.
    setup = 'pip install --upgrade pip && \
        conda init bash && \
        conda activate resnet || \
          (conda create -n resnet python=3.7 -y && \
           conda activate resnet && \
           pip install tensorflow==2.4.0 pyyaml && \
           cd models && pip install -e .)'

    task = sky.Task('setup', workdir=workdir, setup=setup)
    task.set_resources(sky.Resources(sky.AWS(), accelerators={'V100': 1}))
# `detach_run` will only detach the `run` command. The provision and `setup` are
# still blocking.
handle = sky.execute(dag, cluster_name='tb', detach_run=True)

# Run the training task.
with sky.Dag() as dag:

    # The command to run.  Will be run under the working directory.
    run = 'conda activate resnet && mkdir -p resnet-model-dir && \
        export XLA_FLAGS=\'--xla_gpu_cuda_data_dir=/usr/local/cuda/\' && \
        python -u models/official/resnet/resnet_main.py --use_tpu=False \
        --mode=train --train_batch_size=256 --train_steps=250 \
        --iterations_per_loop=125 \
        --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet \
        --model_dir=resnet-model-dir \
        --amp --xla --loss_scale=128'

    train = sky.Task(
        'train',
        workdir=workdir,
        run=run,
    )

    train.set_resources({
        sky.Resources(accelerators='V100'),
    })
sky.execute(dag,
            cluster_name='tb',
            handle=handle,
            stages=[
                sky.execution.Stage.EXEC,
            ],
            detach_run=True)

# Run the tensorboard task.
with sky.Dag() as dag:
    # Use 'ssh -L 4650:localhost:4650 <cluster_name>' to forward port to local.
    # 'ssh -L 4650:localhost:4650 tb'
    tensorboard = sky.Task(
        'tensorboard',
        workdir=workdir,
        setup=setup,
        run='conda activate resnet && \
            tensorboard --logdir resnet-model-dir --port 4650'                                                              ,
    )
    # FIXME: We need to support task without specify resources.
    tensorboard.set_resources(sky.Resources())

sky.execute(dag,
            cluster_name='tb',
            handle=handle,
            stages=[
                sky.execution.Stage.EXEC,
            ],
            detach_run=True)

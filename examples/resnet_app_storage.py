import subprocess

import sky

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '~/Downloads/tpu'

    data_mount_path = '/tmp/imagenet'

    # Clone the repo locally to workdir
    subprocess.run(
        'git clone https://github.com/concretevitamin/tpu '
        f'{workdir} || true',
        shell=True,
        check=True)
    subprocess.run(f'cd {workdir} && git checkout 9459fee',
                   shell=True,
                   check=True)

    # The setup command.  Will be run under the working directory.
    setup = """\
        set -e
        pip install --upgrade pip
        conda init bash
        conda activate resnet && exists=1 || exists=0
        if [ $exists -eq 0 ]; then
            conda create -n resnet python=3.7 -y
            conda activate resnet
            conda install cudatoolkit=11.0 -y
            pip install tensorflow==2.4.0 pyyaml
            pip install protobuf==3.20
            mkdir -p $CONDA_PREFIX/etc/conda/activate.d
            echo 'CUDNN_PATH=$(dirname $(python -c "import nvidia.cudnn;print(nvidia.cudnn.__file__)"))' >> $CONDA_PREFIX/etc/conda/activate.d/env_vars.sh
            echo 'export LD_LIBRARY_PATH=$CONDA_PREFIX/lib/:$CUDNN_PATH/lib:$LD_LIBRARY_PATH' >> $CONDA_PREFIX/etc/conda/activate.d/env_vars.sh
            cd models && pip install -e .
        fi
        """

    # The command to run.  Will be run under the working directory.
    run = f"""\
        conda activate resnet
        export XLA_FLAGS=\'--xla_gpu_cuda_data_dir=/usr/local/cuda/\'
        python -u models/official/resnet/resnet_main.py --use_tpu=False \
            --mode=train --train_batch_size=256 --train_steps=250 \
            --iterations_per_loop=125 \
            --data_dir={data_mount_path} \
            --model_dir=resnet-model-dir \
            --amp --xla --loss_scale=128
        """

    # If the backend to be added is not specified, then SkyPilot's optimizer
    # will choose the backend bucket to be stored.
    # S3 Example
    storage = sky.Storage(source="s3://imagenet-bucket")
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
        sky.Resources(infra='aws', instance_type='p3.2xlarge'),
    })

sky.launch(dag)

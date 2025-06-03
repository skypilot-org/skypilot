import subprocess

import sky

# The working directory contains all code and will be synced to remote.
workdir = '~/Downloads/tpu'

# Clone the repo locally to workdir
subprocess.run(
    'git clone https://github.com/concretevitamin/tpu '
    f'{workdir} || true',
    shell=True,
    check=True)
subprocess.run(f'cd {workdir} && git checkout 9459fee', shell=True, check=True)

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
run = """\
    conda activate resnet
    export XLA_FLAGS=\'--xla_gpu_cuda_data_dir=/usr/local/cuda/\'
    python -u models/official/resnet/resnet_main.py --use_tpu=False \
        --mode=train --train_batch_size=256 --train_steps=250 \
        --iterations_per_loop=125 \
        --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet \
        --model_dir=resnet-model-dir \
        --amp --xla --loss_scale=128
    """

### Optional: download data to VM's local disks. ###
# Format: {VM paths: local paths / cloud URLs}.
file_mounts = {
    # Download from GCS before training starts.
    # '/tmp/fake_imagenet': 'gs://cloud-tpu-test-datasets/fake_imagenet',
}
# Refer to the VM local path.
# run = run.replace('gs://cloud-tpu-test-datasets/fake_imagenet',
#                   '/tmp/fake_imagenet')
### Optional end ###

task = sky.Task(
    'train',
    workdir=workdir,
    setup=setup,
    run=run,
)
task.set_file_mounts(file_mounts)
# TODO: allow option to say (or detect) no download/egress cost.
task.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                estimated_size_gigabytes=70)
task.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
task.set_resources({
    ##### Fully specified
    # sky.Resources(infra='aws', instance_type='p3.2xlarge'),
    # sky.Resources(infra='gcp', instance_type='n1-standard-16'),
    # sky.Resources(
    #     infra='gcp',
    #     'n1-standard-8',
    #     # Options: 'V100', {'V100': <num>}.
    #     'V100',
    # ),
    ##### Partially specified
    # sky.Resources(accelerators='T4'),
    # sky.Resources(accelerators={'T4': 8}, use_spot=True),
    # sky.Resources(infra='aws', accelerators={'T4': 8}, use_spot=True),
    # sky.Resources(infra='aws', accelerators='K80'),
    # sky.Resources(infra='aws', accelerators='K80', use_spot=True),
    # sky.Resources(accelerators='tpu-v3-8'),
    # sky.Resources(accelerators='V100', use_spot=True),
    # sky.Resources(accelerators={'T4': 4}),
    sky.Resources(infra='aws', accelerators='V100'),
    # sky.Resources(infra='gcp', accelerators={'V100': 4}),
    # sky.Resources(infra='aws', accelerators='V100', use_spot=True),
    # sky.Resources(infra='aws', accelerators={'V100': 8}),
})

# Optionally, specify a time estimator: Resources -> time in seconds.
# task.set_time_estimator(time_estimators.resnet50_estimate_runtime)

# sky.launch(task, dryrun=True)
sky.launch(task)

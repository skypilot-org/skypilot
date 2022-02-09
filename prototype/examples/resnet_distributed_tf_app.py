import subprocess
from typing import Dict, List

import sky

IPAddr = str

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '~/Downloads/tpu'
    subprocess.run(f'cd {workdir} && git checkout 222cc86',
                   shell=True,
                   check=True)

    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install --upgrade pip && \
           pip3 install ray[default] awscli botocore boto3 && \
           conda create -n resnet python=3.7 -y && \
           conda activate resnet && \
           pip install tensorflow==2.4.0 pyyaml && \
           cd models && pip install -e .'

    # The command to run.  Will be run under the working directory.
    # If a str, run the same command on all nodes.
    # If a function, run per-node command on each node.
    def run_fn(node_i: int, ip_list: List[IPAddr]) -> str:
        import json
        tf_config = {
            'cluster': {
                'worker': [ip + ':8008' for ip in ip_list]
            },
            'task': {
                'type': 'worker',
                'index': node_i
            }
        }
        str_tf_config = json.dumps(tf_config)
        print(f'{str_tf_config!r}')
        run = f"""
            conda activate resnet
            rm -rf resnet_model-dir
            export TF_CONFIG={str_tf_config!r}
            export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/'
            python models/official/resnet/resnet_main.py --use_tpu=False \
                --mode=train --train_batch_size=256 --train_steps=500 \
                --iterations_per_loop=125 \
                --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet \
                --model_dir=resnet-model-dir \
                --amp --xla --loss_scale=128"""
        return run

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        num_nodes=num_nodes,
        run=run_fn,
    )

    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources(sky.Resources(sky.AWS(), accelerators='V100'))

# sky.launch(dag, dryrun=True)
sky.launch(dag, cluster_name='dtf')
# sky.exec(dag, cluster_name='dtf')

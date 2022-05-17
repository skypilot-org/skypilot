import getpass
import hashlib
import socket
import subprocess
from typing import List, Optional

import sky

# (username, last 4 chars of hash of hostname): for uniquefying users on
# shared-account cloud providers.
hostname_hash = hashlib.md5(socket.gethostname().encode()).hexdigest()[-4:]
_user_and_host = f'{getpass.getuser()}-{hostname_hash}'
cluster = f'test-distributed-tf-{_user_and_host}'

with sky.Dag() as dag:
    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    # The setup command.  Will be run under the working directory.
    setup = """
            git clone https://github.com/concretevitamin/tpu || true
            cd tpu && git checkout 9459fee
            conda create -n resnet python=3.7 -y
            conda activate resnet
            conda install cudatoolkit=11.0 -y
            pip install tensorflow==2.4.0 pyyaml
            cd models && pip install -e .
            """

    # The command to run.  Will be run under the working directory.
    # If a str, run the same command on all nodes.
    # Generates per-node commands.  Must be a self-contained lambda
    # that doesn't refer to any external variables.
    #
    # Will be run under the working directory.
    def run_fn(node_rank: int, ip_list: List[str]) -> Optional[str]:
        import json
        tf_config = {
            'cluster': {
                'worker': [ip + ':8008' for ip in ip_list]
            },
            'task': {
                'type': 'worker',
                'index': node_rank
            }
        }
        str_tf_config = json.dumps(tf_config)
        print(f'{str_tf_config!r}')
        run = f"""
            cd tpu
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
        setup=setup,
        num_nodes=num_nodes,
        run=run_fn,
    )

    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources(sky.Resources(sky.AWS(), accelerators='V100'))

sky.launch(dag, cluster_name=cluster)

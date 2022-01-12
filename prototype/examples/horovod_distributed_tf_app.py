import json
from typing import Dict, List

import sky
import time_estimators
from sky import clouds

IPAddr = str

with sky.Dag() as dag:
    # Total Nodes, INCLUDING Head Node
    num_nodes = 2

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install --upgrade pip && \
           pip3 install ray[default] && \
        git clone https://github.com/michaelzhiluo/horovod-tf-resnet.git && \
        cd horovod-tf-resnet && chmod +x setup.sh && ./setup.sh'

    # Post setup function. Run after `ray up *.yml` completes. Returns dictionary of commands to be run on each corresponding node.
    # List of IPs, 0th index denoting head worker
    def post_setup_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        command_dict = {}
        head_run_str = "ssh-keygen -f ~/.ssh/id_rsa -P \"\" <<< y"
        if len(ip_list) > 1:
            for i, ip in enumerate(ip_list[1:]):
                append_str = f" && cat ~/.ssh/id_rsa.pub | ssh -i ~/ray_bootstrap_key.pem -o StrictHostKeyChecking=no ubuntu@{ip} \"mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys\""
                head_run_str = head_run_str + append_str
        return {ip_list[0]: head_run_str}

    # The command to run.  Will be run under the working directory.
    def run_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        run_dict = {}
        ip_str = "localhost:1"
        for i, ip in enumerate(ip_list[1:]):
            append_str = f",{ip}:1"
            ip_str = ip_str + append_str
        return {
            ip_list[0]: f"cd horovod-tf-resnet && \
            horovodrun -np {str(len(ip_list))} -H {ip_str} python3 horovod_mnist.py",
        }

    run = run_fn

    train = sky.Task(
        'train',
        setup=setup,
        post_setup_fn=post_setup_fn,
        num_nodes=num_nodes,
        run=run,
    )

    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
    })

dag = sky.Optimizer.optimize(dag)
sky.launch(dag)

import json
from typing import Dict, List

import sky
import time_estimators
from sky import clouds

IPAddr = str

##############################
# Options for inputs:
#
#  P0:
#    - read from the specified cloud store, continuously
#    - sync submission-site local files to remote FS
#
#  1. egress from the specified cloud store, to local
#
#  TODO: if egress, what bucket to use for the destination cloud store?
#
##############################
# Options for outputs:
#
#  P0. write to local only (don't destroy VM at the end)
#
#  P1. write to local, sync to a specified cloud's bucket
#
#  P2. continuously write checkpoints to a specified cloud's bucket
#       TODO: this is data egress from run_cloud; not taken into account

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '~/Downloads/tpu'

    docker_image = None  #'rayproject/ray-ml:latest-gpu'
    container_name = None  #'resnet_container'

    # Total Nodes, INCLUDING Head Node
    num_nodes = 2
    # Max number of nodes in Cluster
    max_nodes = 5

    # The setup command.  Will be run under the working directory.
    setup = 'pip3 install --upgrade pip && \
           pip3 install ray[default] awscli botocore boto3 && \
           conda create -n resnet python=3.7 -y && \
           conda activate resnet && \
           pip install tensorflow==2.4.0 pyyaml ray[default] awscli botocore boto3 && \
           cd models && pip install -e .'

    # Post setup function. Run after `ray up *.yml` completes. Returns dictionary of commands to be run on each corresponding node.
    # List of IPs, 0th index denoting head worker
    def post_setup_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        command_dict = {}
        tf_config = {
            'cluster': {
                'worker': [ip + ':8008' for ip in ip_list]
            },
            'task': {
                'type': 'worker',
                'index': -1
            }
        }
        for i, ip in enumerate(ip_list):
            tf_config['task']['index'] = i
            str_tf_config = json.dumps(tf_config).replace('"', '\\"')
            command_dict[
                ip] = "echo \"export TF_CONFIG='" + str_tf_config + "'\" >> ~/.bashrc"
        return command_dict

    # The command to run.  Will be run under the working directory.
    def run_fn(ip_list: List[IPAddr]) -> Dict[IPAddr, str]:
        run_dict = {}
        # TODO: user shouldn't need to specify 'cd /tmp/workdir'
        for i, ip in enumerate(ip_list):
            run_dict[ip] = 'source ~/.bashrc && \
            source activate resnet && \
            rm -rf resnet_model-dir && \
            cd /tmp/workdir && \
            python models/official/resnet/resnet_main.py --use_tpu=False \
            --mode=train --train_batch_size=256 --train_steps=2000 \
            --iterations_per_loop=125 \
            --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet \
            --model_dir=resnet-model-dir \
            --amp --xla --loss_scale=128'

        return run_dict

    run = run_fn

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        post_setup_fn=post_setup_fn,
        docker_image=docker_image,
        container_name=container_name,
        num_nodes=num_nodes,
        max_nodes=max_nodes,
        run=run,
    )

    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
    train.set_resources({
        ##### Fully specified
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
        # sky.Resources(clouds.GCP(), 'n1-standard-16'),
        #sky.Resources(
        #     clouds.GCP(),
        #     'n1-standard-8',
        # Options: 'V100', {'V100': <num>}.
        #     'V100',
        #),
        ##### Partially specified
        #sky.Resources(accelerators='V100'),
        # sky.Resources(accelerators='tpu-v3-8'),
        # sky.Resources(clouds.AWS(), accelerators={'V100': 4}),
        # sky.Resources(clouds.AWS(), accelerators='V100'),
    })

    # Optionally, specify a time estimator: Resources -> time in seconds.
    # train.set_time_estimator(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)

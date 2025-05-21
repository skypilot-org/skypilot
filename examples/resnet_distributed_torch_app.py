from typing import List, Optional

import sky

# Total Nodes, INCLUDING Head Node
num_nodes = 2

# The setup command.  Will be run under the working directory.
setup = 'echo \"alias python=python3\" >> ~/.bashrc && pip3 install --upgrade pip && \
    [ -d pytorch-distributed-resnet ] || \
    (git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet && \
    cd pytorch-distributed-resnet && pip3 install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113 && \
    mkdir -p data  && mkdir -p saved_models && cd data && \
    wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz && \
    tar -xvzf cifar-10-python.tar.gz)'


# The command to run.  Will be run under the working directory.
def run_fn(node_rank: int, ip_list: List[str]) -> Optional[str]:
    num_nodes = len(ip_list)
    return f"""\
    cd pytorch-distributed-resnet
    python3 -m torch.distributed.launch --nproc_per_node=1 \
    --nnodes={num_nodes} --node_rank={node_rank} --master_addr={ip_list[0]} \
    --master_port=8008 resnet_ddp.py --num_epochs 20
    """


train = sky.Task(
    'train',
    setup=setup,
    num_nodes=num_nodes,
    run=run_fn,
)

train.set_resources({
    ##### Fully specified
    sky.Resources(infra='aws', instance_type='p3.2xlarge'),
    # sky.Resources(infra='gcp', instance_type='n1-standard-16'),
    #sky.Resources(
    #     infra='gcp',
    #     instance_type='n1-standard-8',
    # Options: 'V100', {'V100': <num>}.
    #     accelerators='V100',
    #),
    ##### Partially specified
    #sky.Resources(accelerators='V100'),
    # sky.Resources(accelerators='tpu-v3-8'),
    # sky.Resources(infra='aws', accelerators={'V100': 4}),
    # sky.Resources(infra='aws', accelerators='V100'),
})

sky.launch(train, cluster_name='dth')
# sky.exec(train, cluster_name='dth')

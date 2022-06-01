.. _dist-jobs:

Distributed Jobs on Many VMs
================================================

Sky supports multi-node cluster
provisioning and distributed execution on many VMs.

For example, here is a simple PyTorch Distributed training example:

.. code-block:: yaml

  name: resnet-distributed-app

  resources:
    accelerators: V100

  num_nodes: 2

  setup: |
    pip3 install --upgrade pip
    git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet
    cd pytorch-distributed-resnet && pip3 install -r requirements.txt
    mkdir -p data  && mkdir -p saved_models && cd data && \
      wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
    tar -xvzf cifar-10-python.tar.gz

  run: |
    cd pytorch-distributed-resnet

    num_nodes=`echo "$SKY_NODE_IPS" | wc -l`
    master_addr=`echo "$SKY_NODE_IPS" | head -n1`
    python3 -m torch.distributed.launch --nproc_per_node=1 \
      --nnodes=$num_nodes --node_rank=${SKY_NODE_RANK} --master_addr=$master_addr \
      --master_port=8008 resnet_ddp.py --num_epochs 20

In the above, :code:`num_nodes: 2` specifies that this task is to be run on 2
nodes. The :code:`setup` and :code:`run` commands are executed on both nodes.

Sky exposes two environment variables to distinguish per-node commands:

- :code:`SKY_NODE_RANK`: rank (an integer ID from 0 to :code:`num_nodes-1`) of
  the node executing the task
- :code:`SKY_NODE_IPS`: a string of IP addresses of the nodes reserved to execute
  the task, where each line contains one IP address.

  You can retrieve the number of nodes by :code:`echo "$SKY_NODE_IPS" | wc -l`
  and the IP address of the third node by :code:`echo "$SKY_NODE_IPS" | sed -n
  3p`.

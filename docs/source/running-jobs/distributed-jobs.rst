.. _dist-jobs:

Distributed Multi-Node Jobs
================================================

SkyPilot supports multi-node cluster
provisioning and distributed execution on many nodes.

For example, here is a simple PyTorch Distributed training example:

.. code-block:: yaml
   :emphasize-lines: 6-6,21-21,23-26

   name: resnet-distributed-app

   resources:
     accelerators: A100:8

   num_nodes: 2

   setup: |
     pip3 install --upgrade pip
     git clone https://github.com/michaelzhiluo/pytorch-distributed-resnet
     cd pytorch-distributed-resnet
     # SkyPilot's default image on AWS/GCP has CUDA 11.6 (Azure 11.5).
     pip3 install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113
     mkdir -p data  && mkdir -p saved_models && cd data && \
       wget -c --quiet https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
     tar -xvzf cifar-10-python.tar.gz

   run: |
     cd pytorch-distributed-resnet

     MASTER_ADDR=`echo "$SKYPILOT_NODE_IPS" | head -n1`
     torchrun \
      --nnodes=$SKYPILOT_NUM_NODES \
      --master_addr=$MASTER_ADDR \
      --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
      --node_rank=$SKYPILOT_NODE_RANK \
      --master_port=12375 \
       resnet_ddp.py --num_epochs 20

In the above,

- :code:`num_nodes: 2` specifies that this task is to be run on 2 nodes, with each node having 8 A100s;
- The highlighted lines in the ``run`` section show common environment variables that are useful for launching distributed training, explained below.

.. note::

    If you encounter the error :code:`[Errno 24] Too many open files`, this indicates that your process has exceeded the maximum number of open file descriptors allowed by the system. This often occurs in high-load scenarios, e.g., launching significant amount number of nodes, such as 100.

    To resolve this issue, run the following command, and try again:

    ::

        ulimit -n 65535


Environment variables
-----------------------------------------

SkyPilot exposes these environment variables that can be accessed in a task's ``run`` commands:

- :code:`SKYPILOT_NODE_RANK`: rank (an integer ID from 0 to :code:`num_nodes-1`) of
  the node executing the task.
- :code:`SKYPILOT_NODE_IPS`: a string of IP addresses of the nodes reserved to execute
  the task, where each line contains one IP address.
- :code:`SKYPILOT_NUM_NODES`: number of nodes reserved for the task, which can be specified by ``num_nodes: <n>``. Same value as :code:`echo "$SKYPILOT_NODE_IPS" | wc -l`.
- :code:`SKYPILOT_NUM_GPUS_PER_NODE`: number of GPUs reserved on each node to execute the
  task; the same as the count in ``accelerators: <name>:<count>`` (rounded up if a fraction).

See :ref:`sky-env-vars` for more details.

Launching a multi-node task (new cluster)
-------------------------------------------------

When using ``sky launch`` to launch a multi-node task on **a new cluster**, the following happens in sequence:

1. Nodes are provisioned. (barrier)
2. Workdir/file_mounts are synced to all nodes. (barrier)
3. ``setup`` commands are executed on all nodes. (barrier)
4. ``run`` commands are executed on all nodes.

Launching a multi-node task (existing cluster)
-------------------------------------------------

When using ``sky launch`` to launch a multi-node task on **an existing cluster**, the cluster may have more nodes than the current task's ``num_nodes`` requirement.

The following happens in sequence:

1. SkyPilot checks the runtime on all nodes are up-to-date. (barrier)
2. Workdir/file_mounts are synced to all nodes. (barrier)
3. ``setup`` commands are executed on **all nodes** of the cluster. (barrier)
4. ``run`` commands are executed on **the subset of nodes** scheduled to execute the task, which may be fewer than the cluster size.

.. tip::

  To skip rerunning the setup commands, use either ``sky launch --no-setup ...``
  (performs steps 1, 2, 4 above) or ``sky exec`` (performs step 2 (workdir only)
  and step 4).

Executing a task on the head node only
--------------------------------------
To execute a task on the head node only (a common scenario for tools like
``mpirun``), use the ``SKYPILOT_NODE_RANK`` environment variable as follows:

.. code-block:: yaml

   ...

   num_nodes: <n>

   run: |
     if [ "${SKYPILOT_NODE_RANK}" == "0" ]; then
         # Launch the head-only command here.
     fi


SSH into worker nodes
---------------------
In addition to the head node, the SSH configurations for the worker nodes of a multi-node cluster are also added to ``~/.ssh/config`` as ``<cluster_name>-worker<n>``.
This allows you directly to SSH into the worker nodes, if required.

.. code-block:: console

  # Assuming 3 nodes in a cluster named mycluster

  # Head node.
  $ ssh mycluster

  # Worker nodes.
  $ ssh mycluster-worker1
  $ ssh mycluster-worker2


Executing a Distributed Ray Program
------------------------------------
To execute a distributed Ray program on many nodes, you can download the `training script <https://github.com/skypilot-org/skypilot/blob/master/examples/distributed_ray_train/train.py>`_ and launch the `task yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/distributed_ray_train/ray_train.yaml>`_:

.. code-block:: console

  $ wget https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/distributed_ray_train/train.py
  $ sky launch ray_train.yaml

.. code-block:: yaml
  
    resources:
      accelerators: L4:2
      memory: 64+
  
    num_nodes: 2

    workdir: .

    setup: |
      conda activate ray
      if [ $? -ne 0 ]; then
        conda create -n ray python=3.10 -y
        conda activate ray
      fi
      
      pip install "ray[train]"
      pip install tqdm
      pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
  
    run: |
      sudo chmod 777 -R /var/tmp
      HEAD_IP=`echo "$SKYPILOT_NODE_IPS" | head -n1`
      if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
        ps aux | grep ray | grep 6379 &> /dev/null || ray start --head  --disable-usage-stats --port 6379
        sleep 5
        python train.py --num-workers $SKYPILOT_NUM_NODES
      else
        sleep 5
        ps aux | grep ray | grep 6379 &> /dev/null || ray start --address $HEAD_IP:6379 --disable-usage-stats
      fi

.. warning:: 

  When using Ray, avoid calling ``ray stop`` as that will also cause the SkyPilot runtime to be stopped.


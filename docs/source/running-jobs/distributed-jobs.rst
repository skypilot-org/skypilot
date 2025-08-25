.. _dist-jobs:

Distributed Multi-Node Jobs
================================================

SkyPilot supports multi-node cluster
provisioning and distributed execution on many nodes.

For example, here is a simple example to train a GPT-like model (inspired by Karpathy's `minGPT <https://github.com/karpathy/minGPT>`_) across 2 nodes with Distributed Data Parallel (DDP) in PyTorch.

.. code-block:: yaml
  :emphasize-lines: 6,19,23-24,26

  name: minGPT-ddp

  resources:
      accelerators: A100:8

  num_nodes: 2

  setup: |
      git clone --depth 1 https://github.com/pytorch/examples || true
      cd examples
      git filter-branch --prune-empty --subdirectory-filter distributed/minGPT-ddp
      # SkyPilot's default image on AWS/GCP has CUDA 11.6 (Azure 11.5).
      uv venv --python 3.10
      source .venv/bin/activate
      uv pip install -r requirements.txt "numpy<2" "torch==1.12.1+cu113" --extra-index-url https://download.pytorch.org/whl/cu113

  run: |
      cd examples
      source .venv/bin/activate
      cd mingpt
      export LOGLEVEL=INFO

      MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
      echo "Starting distributed training, head node: $MASTER_ADDR"

      torchrun \
      --nnodes=$SKYPILOT_NUM_NODES \
      --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
      --master_addr=$MASTER_ADDR \
      --node_rank=${SKYPILOT_NODE_RANK} \
      --master_port=8008 \
      main.py


In the above,

- :code:`num_nodes: 2` specifies that this job is to be run on 2 nodes, with each node having 8 A100s;
- The highlighted lines in the ``run`` section show common environment variables that are useful for launching distributed training, explained below.

.. note::

    If you encounter the error :code:`[Errno 24] Too many open files`, this indicates that your process has exceeded the maximum number of open file descriptors allowed by the system. This often occurs in high-load scenarios, e.g., launching significant amount number of nodes, such as 100.

    To resolve this issue, run the following command, and try again:

    ::

        ulimit -n 65535

You can find more `distributed training examples <https://github.com/skypilot-org/skypilot/tree/master/examples/distributed-pytorch>`_ (including `using rdvz backend for pytorch <https://github.com/skypilot-org/skypilot/blob/master/examples/distributed-pytorch/train-rdzv.yaml>`_) in our `GitHub repository <https://github.com/skypilot-org/skypilot/tree/master/examples>`_.

Unless otherwise specified, descriptions below apply to both :ref:`cluster jobs <job-queue>` and :ref:`managed jobs <managed-jobs>`.

Environment variables
-----------------------------------------

SkyPilot exposes these environment variables that can be accessed in a job's ``run`` commands:

- :code:`SKYPILOT_NODE_RANK`: rank (an integer ID from 0 to :code:`num_nodes-1`) of
  the node executing the job.
- :code:`SKYPILOT_NODE_IPS`: a string of IP addresses of the nodes reserved to execute
  the job, where each line contains one IP address.
- :code:`SKYPILOT_NUM_NODES`: number of nodes reserved for the job, which can be specified by ``num_nodes: <n>``. Same value as :code:`echo "$SKYPILOT_NODE_IPS" | wc -l`.
- :code:`SKYPILOT_NUM_GPUS_PER_NODE`: number of GPUs reserved on each node to execute the
  job; the same as the count in ``accelerators: <name>:<count>`` (rounded up if a fraction).

See :ref:`sky-env-vars` for more details.

Launching a multi-node job (new cluster)
-------------------------------------------------

When using ``sky launch`` to launch a multi-node job on **a new cluster**, the following happens in sequence:

1. Nodes are provisioned. (barrier)
2. Workdir/file_mounts are synced to all nodes. (barrier)
3. ``setup`` commands are executed on all nodes. (barrier)
4. ``run`` commands are executed on all nodes.

When using ``sky jobs launch`` to launch a :ref:`managed <managed-jobs>` multi-node job, the same behavior applies.

Launching a multi-node job (existing cluster)
-------------------------------------------------

When using ``sky launch`` to launch a multi-node job on **an existing cluster**, the cluster may have more nodes than the current job's ``num_nodes`` requirement.

The following happens in sequence:

1. SkyPilot checks the runtime on all nodes are up-to-date. (barrier)
2. Workdir/file_mounts are synced to all nodes. (barrier)
3. ``setup`` commands are executed on **all nodes** of the cluster. (barrier)
4. ``run`` commands are executed on **the subset of nodes** scheduled to execute the job, which may be fewer than the cluster size.

.. tip::

  To skip rerunning the setup commands, use either ``sky launch --no-setup ...``
  (performs steps 1, 2, 4 above) or ``sky exec`` (performs step 2 (workdir only)
  and step 4).

Executing a job on the head node only
--------------------------------------
To execute a job on the head node only (a common scenario for tools like
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
In addition to the head node, the SSH configuration values for the worker nodes of a multi-node cluster are also added to ``~/.ssh/config`` as ``<cluster_name>-worker<n>``.
This allows you directly to SSH into the worker nodes, if required.

.. code-block:: console

  # Assuming 3 nodes in a cluster named mycluster

  # Head node.
  $ ssh mycluster

  # Worker nodes.
  $ ssh mycluster-worker1
  $ ssh mycluster-worker2

SSH access is only available for :ref:`clusters <dev-cluster>` (designed for interactive development), not for :ref:`managed jobs <managed-jobs>` (designed for production, scale-out runs).

Executing a distributed Ray program
------------------------------------

.. note::

   **Important**: Always start your own Ray cluster for user workloads. SkyPilot uses Ray internally on port 6380 for cluster management. Using ``ray.init(address="auto")`` connects to SkyPilot's internal Ray cluster, causing resource conflicts. The example below starts a separate Ray cluster on port 6379.

To execute a distributed Ray program on many nodes, you can download the `training script <https://github.com/skypilot-org/skypilot/blob/master/examples/distributed_ray_train/train.py>`_ and launch the `job yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/distributed_ray_train/ray_train.yaml>`_:

.. code-block:: console

  $ wget https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/distributed_ray_train/train.py

  $ # Use a cluster (ideal for interactive development)
  $ sky launch ray_train.yaml

  $ # Use a managed job (ideal for production, scale-out runs)
  $ sky jobs launch ray_train.yaml

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
        # Add sleep to after `ray start` to give ray enough time to daemonize
        sleep 5
      fi

.. warning::

  When using Ray, avoid calling ``ray stop`` as that will also cause the SkyPilot runtime to be stopped.


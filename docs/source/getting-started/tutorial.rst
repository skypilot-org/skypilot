.. _ai-training:

Quickstart: PyTorch
===================
This example uses SkyPilot to train a GPT-like model (inspired by Karpathy's `minGPT <https://github.com/karpathy/minGPT>`_) with Distributed Data Parallel (DDP) in PyTorch.

.. tab-set::

  .. tab-item:: CLI

    We define a :ref:`SkyPilot YAML <yaml-spec>` with the resource requirements, the setup commands,
    and the commands to run:

    .. code-block:: yaml

      # train.yaml

      name: minGPT-ddp

      resources:
          cpus: 4+
          accelerators: L4:4  # Or A100:8, H100:8

      # Optional: upload a working directory to remote ~/sky_workdir.
      # Commands in "setup" and "run" will be executed under it.
      #
      # workdir: .

      # Optional: upload local files.
      # Format:
      #   /remote/path: /local/path
      #
      # file_mounts:
      #   ~/.vimrc: ~/.vimrc
      #   ~/.netrc: ~/.netrc

      setup: |
          git clone --depth 1 https://github.com/pytorch/examples || true
          cd examples
          git filter-branch --prune-empty --subdirectory-filter distributed/minGPT-ddp
          uv pip install --system -r requirements.txt

      run: |
          cd examples/mingpt
          export LOGLEVEL=INFO

          echo "Starting minGPT-ddp training"

          torchrun \
          --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
          main.py

    .. tip::

      In the YAML, the ``workdir`` and ``file_mounts`` fields are commented out. To
      learn about how to use them to mount local dirs/files or object store buckets
      (S3, GCS, R2) into your cluster, see :ref:`sync-code-artifacts`.

    .. tip::

      The ``SKYPILOT_NUM_GPUS_PER_NODE`` environment variable is automatically set by SkyPilot to the number of GPUs per node. See :ref:`env-vars` for more.

    Then, launch training:

    .. code-block:: console

      $ sky launch -c mingpt train.yaml
  
  .. tab-item:: Python

    We use the :ref:`Python SDK <pythonapi>` to create a task with the resource requirements, the setup commands,
    and the commands to run:

    .. code-block:: python

      # train.py

      import sky

      minGPT_ddp_task = sky.Task(
          name='minGPT-ddp',
          resources=sky.Resources(
              cpus='4+',
              accelerators='L4:4',
          ),
          # Optional: upload a working directory to remote ~/sky_workdir.
          # Commands in "setup" and "run" will be executed under it.
          #
          # workdir='.',
          #
          # Optional: upload local files.
          # Format:
          #   /remote/path: /local/path
          #
          # file_mounts={
          #     '~/.vimrc': '~/.vimrc',
          #     '~/.netrc': '~/.netrc',
          # },
          setup=[
              'git clone --depth 1 https://github.com/pytorch/examples || true',
              'cd examples',
              'git filter-branch --prune-empty --subdirectory-filter distributed/minGPT-ddp',
              'uv pip install --system -r requirements.txt',
          ],
          run=[
              'cd examples/mingpt',
              'export LOGLEVEL=INFO',
              'torchrun --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE main.py',
          ]
      )

      cluster_name = 'mingpt'
      launch_request = sky.launch(task=minGPT_ddp_task, cluster_name=cluster_name)
      job_id, _ = sky.stream_and_get(launch_request)
      sky.tail_logs(cluster_name, job_id, follow=True)

    .. tip::

      In the code, the ``workdir`` and ``file_mounts`` fields are commented out. To
      learn about how to use them to mount local dirs/files or object store buckets
      (S3, GCS, R2) into your cluster, see :ref:`sync-code-artifacts`.

    .. tip::

      The ``SKYPILOT_NUM_GPUS_PER_NODE`` environment variable is automatically set by SkyPilot to the number of GPUs per node. See :ref:`env-vars` for more.

    Then, run the code:

    .. code-block:: console

      $ python train.py

    
This will provision the cheapest cluster with the required resources, execute the setup
commands, then execute the run commands.

After the training job starts running, you can safely :code:`Ctrl-C` to detach
from logging and the job will continue to run remotely on the cluster.  To stop
the job, use the :code:`sky cancel <cluster_name> <job_id>` command (refer to :ref:`CLI reference <cli>`).

After training, :ref:`transfer artifacts <sync-code-artifacts>` such
as logs and checkpoints using familiar tools.

.. tip::

  Feel free to copy-paste the YAML or Python code above and customize it for
  your own project.

Scale up the training with multiple nodes
-----------------------------------------

To scale training across multiple nodes, add ``num_nodes`` to your task YAML:

.. code-block:: yaml
  :emphasize-lines: 5

  resources:
      cpus: 4+
      accelerators: L4:4

  # Use 2 nodes with 4 GPUs each (8 GPUs total)
  num_nodes: 2

And update the run command to use torchrun's multi-node flags:

.. code-block:: yaml

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
      --master_port=8008 \
      --node_rank=${SKYPILOT_NODE_RANK} \
      main.py

.. tip::

  SkyPilot automatically sets these environment variables for distributed training:

  - ``SKYPILOT_NUM_NODES``: Total number of nodes
  - ``SKYPILOT_NUM_GPUS_PER_NODE``: Number of GPUs per node
  - ``SKYPILOT_NODE_RANK``: Rank of the current node (0, 1, 2, ...)
  - ``SKYPILOT_NODE_IPS``: List of IP addresses of all nodes

  See :ref:`env-vars` for more.

More details in: `Distributed Training with PyTorch <https://docs.skypilot.co/en/latest/examples/training/distributed-pytorch.html>`_.

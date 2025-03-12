.. _ai-training:

Quickstart: PyTorch
======================
This example uses SkyPilot to train a GPT-like model (inspired by Karpathy's `minGPT <https://github.com/karpathy/minGPT>`_) with Distributed Data Parallel (DDP) in PyTorch.

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
      # SkyPilot's default image on AWS/GCP has CUDA 11.6 (Azure 11.5).
      uv pip install -r requirements.txt "numpy<2" "torch==1.12.1+cu113" --extra-index-url https://download.pytorch.org/whl/cu113

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

This will provision the cheapest cluster with the required resources, execute the setup
commands, then execute the run commands.

After the training job starts running, you can safely :code:`Ctrl-C` to detach
from logging and the job will continue to run remotely on the cluster.  To stop
the job, use the :code:`sky cancel <cluster_name> <job_id>` command (refer to :ref:`CLI reference <cli>`).

After training, :ref:`transfer artifacts <sync-code-artifacts>` such
as logs and checkpoints using familiar tools.

.. tip::

  Feel free to copy-paste the YAML above and customize it for
  your own project.

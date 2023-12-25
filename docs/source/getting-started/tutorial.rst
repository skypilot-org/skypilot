.. _dnn-training:

Tutorial: DNN Training
======================
This example uses SkyPilot to train a Transformer-based language model from HuggingFace.

First, define a :ref:`task YAML <yaml-spec>` with the resource requirements, the setup commands,
and the commands to run:

.. code-block:: yaml

  # dnn.yaml

  name: huggingface

  resources:
    accelerators: V100:4

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
    set -e  # Exit if any command failed.
    git clone https://github.com/huggingface/transformers/ || true
    cd transformers
    pip install .
    cd examples/pytorch/text-classification
    pip install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113

  run: |
    set -e  # Exit if any command failed.
    cd transformers/examples/pytorch/text-classification
    python run_glue.py \
      --model_name_or_path bert-base-cased \
      --dataset_name imdb  \
      --do_train \
      --max_seq_length 128 \
      --per_device_train_batch_size 32 \
      --learning_rate 2e-5 \
      --max_steps 50 \
      --output_dir /tmp/imdb/ --overwrite_output_dir \
      --fp16

.. tip::

  In the YAML, the ``workdir`` and ``file_mounts`` fields are commented out. To
  learn about how to use them to mount local dirs/files or object store buckets
  (S3, GCS, R2) into your cluster, see :ref:`sync-code-artifacts`.

Then, launch training:

.. code-block:: console

   $ sky launch -c lm-cluster dnn.yaml

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

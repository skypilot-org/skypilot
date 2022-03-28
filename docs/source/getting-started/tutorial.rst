.. _huggingface:
Tutorial: DNN Training
======================
This example uses Sky to train a Transformer-based language model from HuggingFace.

First, define a task YAML with resource requirements, the setup commands,
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
    pip3 install .
    cd examples/pytorch/text-classification
    pip3 install -r requirements.txt

  run: |
    set -e  # Exit if any command failed.
    cd transformers/examples/pytorch/text-classification
    python3 run_glue.py \
      --model_name_or_path bert-base-cased \
      --dataset_name imdb  \
      --do_train \
      --max_seq_length 128 \
      --per_device_train_batch_size 32 \
      --learning_rate 2e-5 \
      --max_steps 50 \
      --output_dir /tmp/imdb/ --overwrite_output_dir \
      --fp16


Then, launch training:

.. code-block:: console

   $ sky launch -c lm-cluster dnn.yaml

This will provision a cluster with the required resources, execute the setup
commands, then execute the run commands.

After the training job starts running, you can safely :code:`Ctrl-C` to detach
from logging and the job will continue to run remotely on the cluster.  To stop
the job, use the :code:`sky cancel <cluster_name> <job_id>` command (refer to :ref:`CLI reference <cli>`). 

After training, :ref:`transfer artifacts <sync-code-artifacts>` such
as logs and checkpoints using familiar tools.

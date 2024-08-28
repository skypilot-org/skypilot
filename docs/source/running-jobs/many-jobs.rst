
.. _many-jobs:

Guide: Run Many Jobs
====================

SkyPilot allows you to easily **run many jobs in parallel** and manage them in a single system. This is useful for hyperparameter tuning sweeps, data processing, and other batch jobs.

We show a typical workflow for running many jobs with SkyPilot.

Develop a YAML for One Job
-----------------------------------

Before scaling up to many jobs, develeoping and debugging a SkyPilot YAML for a single job can save a lot of time by avoiding debugging many jobs at once.

We use the same example of AI model training as in :ref:`Tutorial: DNN Training <dnn-training>`.

.. raw:: html

    <details>
    <summary>Click to expand: <code>dnn.yaml</code></summary>

.. code-block:: yaml

  # dnn.yaml
  name: huggingface

  resources:
    accelerators: V100:4

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


.. raw:: html

    </details>


We can launch the job with the following command to check the correctness of the YAML.

.. code-block:: bash

  sky launch -c dnn dnn.yaml


If there is any error, we can fix the YAML and launch the job again on the same cluster.

.. code-block:: bash

  # Cancel the latest job.
  sky cancel dnn -y
  # Run the job again on the same cluster.
  sky launch -c dnn dnn.yaml


Sometimes, we may find it more efficient to interactively log into the cluster and debug the job. We can do so by directly :ref:`ssh into the cluster or use VSCode's remote ssh <dev-connect>`.

.. code-block:: bash

  # Log into the cluster.
  ssh dnn



After confirming the job is working correctly, we now start **adding additional fields** to make the job YAML more configurable.

1. Add Hyperparameters
~~~~~~~~~~~~~~~~~~~~~~

To launch many jobs with different hyperparameters, we turn the SkyPilot YAML into a template, by
adding :ref:`environment variables <env-vars>` as arguments for the job.

.. raw:: html

    <details>
    <summary>Updated SkyPilot YAML: <code>dnn-template.yaml</code></summary>

.. code-block:: yaml
  :emphasize-lines: 4-6,28-29

  # dnn-template.yaml
  name: huggingface

  envs:
    LR: 2e-5
    MAX_STEPS: 50
    
  resources:
    accelerators: V100:4

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
      --learning_rate ${LR} \
      --max_steps ${MAX_STEPS} \
      --output_dir /tmp/imdb/ --overwrite_output_dir \
      --fp16

.. raw:: html
    
    </details>

We can now launch a job with different hyperparameters by specifying the envs.

.. code-block:: bash

  sky launch -c dnn dnn-template.yaml \
    --env LR=1e-5 \
    --env MAX_STEPS=100

Or, you can store the envs in a dotenv file and launch the job with the file: ``configs/job1.env``.

.. code-block:: bash

  # configs/job1.env
  LR=1e-5
  MAX_STEPS=100

.. code-block:: bash

  sky launch -c dnn dnn-template.yaml \
    --env-file configs/job1.env



2. Track Job Output
~~~~~~~~~~~~~~~~~~~

When running many jobs, it is useful to track live outputs of each job. We recommend using WandB to track the outputs of all jobs.

.. raw:: html

    <details>
    <summary>SkyPilot YAML with WandB: <code>dnn-template.yaml</code></summary>

.. code-block:: yaml
  :emphasize-lines: 7-7,19-19,34-34

  # dnn-template.yaml
  name: huggingface

  envs:
    LR: 2e-5
    MAX_STEPS: 50
    WANDB_API_KEY: # Empty field means this field is required when launching the job.
      
  resources:
    accelerators: V100:4

  setup: |
    set -e  # Exit if any command failed.
    git clone https://github.com/huggingface/transformers/ || true
    cd transformers
    pip install .
    cd examples/pytorch/text-classification
    pip install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113
    pip install wandb

  run: |
    set -e  # Exit if any command failed.
    cd transformers/examples/pytorch/text-classification
    python run_glue.py \
      --model_name_or_path bert-base-cased \
      --dataset_name imdb  \
      --do_train \
      --max_seq_length 128 \
      --per_device_train_batch_size 32 \
      --learning_rate ${LR} \
      --max_steps ${MAX_STEPS} \
      --output_dir /tmp/imdb/ --overwrite_output_dir \
      --fp16 \
      --report_to wandb

.. raw:: html

    </details>

We can now launch the job with the following command (``WANDB_API_KEY`` should existing in your local environment variables).

.. code-block:: bash

  sky launch -c dnn dnn-template.yaml \
    --env-file configs/job1.env \
    --env WANDB_API_KEY



Scale up the Job
-----------------

With the above setup, we can now scale up a job to many in-parallel jobs by creating multiple config files and
submitting them with :ref:`SkyPilot managed jobs <managed-jobs>`.

We create a config file for each job in the ``configs`` directory.

.. code-block:: bash

  # configs/job1.env
  LR=1e-5
  MAX_STEPS=100

  # configs/job2.env
  LR=2e-5
  MAX_STEPS=200

  ...

We can then submit all jobs by iterating over the config files.

.. code-block:: bash

  for config_file in configs/*.env; do
    job_name=$(basename ${config_file%.env})
    # -y means yes to all prompts.
    # -d means detach from the job's logging, so the next job can be submitted
    # without waiting for the previous job to finish.
    sky jobs launch -n dnn-$job_name -y -d dnn-template.yaml \
      --env-file $config_file \
      --env WANDB_API_KEY
  done


All job statuses can be checked with the following command.

.. code-block:: console

  $ sky jobs queue

  Fetching managed job statuses...
  Managed jobs
  In progress tasks: 3 RUNNING
  ID  TASK  NAME      RESOURCES  SUBMITTED    TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS   
  10  -     dnn-job10 1x[V100:4] 5 mins ago   5m 5s          1m 12s        0            RUNNING
  9   -     dnn-job9  1x[V100:4] 6 mins ago   6m 11s         2m 23s        0            RUNNING
  8   -     dnn-job8  1x[V100:4] 7 mins ago   7m 15s         3m 31s        0            RUNNING
  ...


.. _many-jobs:

Many Parallel Jobs
======================

SkyPilot allows you to easily **run many jobs in parallel** and manage them in a single system. This is useful for hyperparameter tuning sweeps, data processing, and other batch jobs.

This guide shows a typical workflow for running many jobs with SkyPilot.


.. image:: https://i.imgur.com/tvxeNyR.png
  :width: 90%
  :align: center
.. TODO: Show the components in a GIF.


Why use SkyPilot to run many jobs
-------------------------------------

- **Unified**: Use any or multiple of your own infrastructure (Kubernetes, cloud VMs, reservations, etc.).
- **Elastic**: Scale up and down based on demands.
- **Cost-effective**: Only pay for the cheapest resources.
- **Robust**: Automatically recover jobs from failures.
- **Observable**: Monitor and manage all jobs in a single pane of glass.

Write a YAML for one job
-----------------------------------

Before scaling up to many jobs, write a SkyPilot YAML for a single job first and ensure it runs correctly. This can save time by avoiding debugging many jobs at once.

Here is the same example YAML as in :ref:`Tutorial: AI Training <ai-training>`:

.. raw:: html

    <details>
    <summary>Click to expand: <code>train.yaml</code></summary>

.. code-block:: yaml

  # train.yaml
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


First, launch the job to check it successfully launches and runs correctly:

.. code-block:: bash

  sky launch -c train train.yaml


If there is any error, you can fix the code and/or the YAML, and launch the job again on the same cluster:

.. code-block:: bash

  # Cancel the latest job.
  sky cancel train -y
  # Run the job again on the same cluster.
  sky launch -c train train.yaml


Sometimes, it may be more efficient to log into the cluster and interactively debug the job. You can do so by directly :ref:`ssh'ing into the cluster or using VSCode's remote ssh <dev-connect>`.

.. code-block:: bash

  # Log into the cluster.
  ssh train



Next, after confirming the job is working correctly, **add (hyper)parameters** to the job YAML so that all job variants can be specified.

1. Add hyperparameters
~~~~~~~~~~~~~~~~~~~~~~

To launch jobs with different hyperparameters, add them as :ref:`environment variables <env-vars>` to the SkyPilot YAML, and make your main program read these environment variables:

.. raw:: html

    <details>
    <summary>Updated SkyPilot YAML: <code>train-template.yaml</code></summary>

.. code-block:: yaml
  :emphasize-lines: 4-6,28-29

  # train-template.yaml
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

You can now use ``--env`` to launch a job with different hyperparameters:

.. code-block:: bash

  sky launch -c train train-template.yaml \
    --env LR=1e-5 \
    --env MAX_STEPS=100

Alternative, store the environment variable values in a dotenv file and use ``--env-file`` to launch:

.. code-block:: bash

  # configs/job1
  LR=1e-5
  MAX_STEPS=100

.. code-block:: bash

  sky launch -c train train-template.yaml \
    --env-file configs/job1



2. Logging job outputs
~~~~~~~~~~~~~~~~~~~~~~~

When running many jobs, it is useful to log the outputs of all jobs. You can use tools like `W&B <https://wandb.ai>`__ for this purpose:

.. raw:: html

    <details>
    <summary>SkyPilot YAML with W&B: <code>train-template.yaml</code></summary>

.. code-block:: yaml
  :emphasize-lines: 7-7,19-19,34-34

  # train-template.yaml
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

You can now launch the job with the following command (``WANDB_API_KEY`` should existing in your local environment variables).

.. code-block:: bash

  sky launch -c train train-template.yaml \
    --env-file configs/job1 \
    --env WANDB_API_KEY


.. _many-jobs-scale-out:

Scale out to many jobs
-----------------------

With the above setup, you can now scale out to run many jobs in parallel.

To run many jobs at once, we will launch the jobs as :ref:`SkyPilot managed jobs <managed-jobs>`. We can control the hyperparameter environment variables independently for each managed job.

You can use normal loops in bash or Python to iterate over possible hyperparameters:

.. tab-set::

    .. tab-item:: CLI
        :sync: cli

        .. code-block:: bash

          job_idx=0
          for lr in 0.01 0.03 0.1 0.3 1.0; do
              for max_steps in 100 300 1000; do
                  sky jobs launch -n train-job${job_idx} -y --async \
                    train-template.yaml \
                    --env LR="${lr}" --env MAX_STEPS="${max_steps}" \
                    --env WANDB_API_KEY # pick up from environment
                  ((job_idx++))
              done
          done

    .. tab-item:: Python
        :sync: python

        .. code-block:: python

          import os
          import sky

          LR_CANDIDATES = [0.01, 0.03, 0.1, 0.3, 1.0]
          MAX_STEPS_CANDIDATES = [100, 300, 1000]
          task = sky.Task.from_yaml('train-template.yaml')

          job_idx = 1
          requests_ids = []
          for lr in LR_CANDIDATES:
            for max_steps in MAX_STEPS_CANDIDATES:
              task.update_envs({'LR': lr, 'MAX_STEPS': max_steps})
              requests_ids.append(
                sky.jobs.launch(
                  task,
                  name=f'train-job{job_idx}',
                )
              )
              job_idx += 1

          # Wait for all jobs to finish
          for request_id in requests_ids:
            sky.get(request_id)

The launched jobs will "detach" once submitted (``-d``), and will run in parallel.

Job statuses can be checked via ``sky jobs queue``:

.. code-block:: console

  $ sky jobs queue

  Fetching managed jobs...
  Managed jobs
  In progress tasks: 10 RUNNING
  ID  TASK  NAME        REQUESTED   SUBMITTED    TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
  10  -     train-job10 1x[V100:4]  5 mins ago   5m 5s          1m 12s        0            RUNNING
  9   -     train-job9  1x[V100:4]  6 mins ago   6m 11s         2m 23s        0            RUNNING
  8   -     train-job8  1x[V100:4]  7 mins ago   7m 15s         3m 31s        0            RUNNING
  ...


With config files
~~~~~~~~~~~~~~~~~

For more control, you can also create specific env var config files.

First, create a config file for each job (for example, in a ``configs`` directory):

.. code-block:: bash

  # configs/job-1
  LR=1e-5
  MAX_STEPS=100

  # configs/job-2
  LR=2e-5
  MAX_STEPS=200

  ...

.. raw:: html

  <details>
  <summary>An example Python script to generate config files</summary>

.. code-block:: python

  import os

  CONFIG_PATH = 'configs'
  LR_CANDIDATES = [0.01, 0.03, 0.1, 0.3, 1.0]
  MAX_STEPS_CANDIDATES = [100, 300, 1000]

  os.makedirs(CONFIG_PATH, exist_ok=True)

  job_idx = 1
  for lr in LR_CANDIDATES:
    for max_steps in MAX_STEPS_CANDIDATES:
      config_file = f'{CONFIG_PATH}/job-{job_idx}'
      with open(config_file, 'w') as f:
        print(f'LR={lr}', file=f)
        print(f'MAX_STEPS={max_steps}', file=f)
      job_idx += 1

.. raw:: html

  </details>

Then, submit all jobs by iterating over the config files and calling ``sky jobs launch`` on each:

.. code-block:: bash

  for config_file in configs/*; do
    job_name=$(basename $config_file)
    # -y: yes to all prompts.
    # -d: detach from the job's logging, so the next job can be submitted
    #      without waiting for the previous job to finish.
    sky jobs launch -n train-$job_name -y --async \
      train-template.yaml \
      --env-file $config_file \
      --env WANDB_API_KEY
  done


Best practices for scaling
--------------------------

By default, around 90 jobs can be managed at once. However, with some simple configuration, SkyPilot can reliably support **2000 jobs running in parallel**. See :ref:`the best practices <jobs-controller-sizing>` for more info.

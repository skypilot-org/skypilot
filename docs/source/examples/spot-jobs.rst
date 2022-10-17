.. _spot-jobs:

Managed Spot Jobs
================================================

SkyPilot supports managed spot jobs that can **automatically recover from preemptions**.
This feature **saves significant cost** (e.g., up to 70\% for GPU VMs) by making preemptible spot instances practical for long-running jobs.

To maximize availability, SkyPilot automatically finds available spot resources across regions and clouds.
Here is an example of BERT training job failing over different regions across AWS and GCP.

.. image:: ../images/spot-training.png
  :width: 600
  :alt: BERT training on Spot V100

Below are requirements for using managed spot jobs:

(1) **Working task YAML**: Managed Spot requires a YAML to describe the job, working with :code:`sky launch`.
(2) **Checkpointing and resuming**: Application code can checkpoint periodically to a :ref:`SkyPilot Storage <sky-storage>`-mounted cloud bucket. For job recovery, the program can reload the latest checkpoint from the same path when restarted.


Working Task YAML
-----------------

To launch a spot job, you can simply reuse your previous task YAML.
For example, we found the BERT fine-tuning YAML works with :code:`sky launch`, and want to
launch it with SkyPilot managed spot jobs. 

We can simply launch it with:

.. code-block:: console

    $ sky spot launch -n bert-qa bert_qa.yaml

.. code-block:: yaml

  # bert_qa.yaml
  name: bert_qa
  
  resources:
    accelerators: V100:1

  # Assume your working directory is under `~/transformers`.
  # To make this example work, please run the following command:
  # git clone https://github.com/huggingface/transformers.git ~/transformers
  workdir: ~/transformers

  setup: |
    # Fill in your wandb key: copy from https://wandb.ai/authorize
    # Alternatively, you can use `--env WANDB_API_KEY=$WANDB_API_KEY`
    # to pass the key in the command line, during `sky spot launch`.
    echo export WANDB_API_KEY=[YOUR-WANDB-API-KEY] >> ~/.bashrc

    git checkout v4.18.0
    pip install -e .
    cd examples/pytorch/question-answering/
    pip install -r requirements.txt
    pip install wandb

  run: |
    cd ./examples/pytorch/question-answering/
    python run_qa.py \
    --model_name_or_path bert-base-uncased \
    --dataset_name squad \
    --do_train \
    --do_eval \
    --per_device_train_batch_size 12 \
    --learning_rate 3e-5 \
    --num_train_epochs 50 \
    --max_seq_length 384 \
    --doc_stride 128 \
    --report_to wandb

.. note::

  :ref:`workdir <sync-code-artifacts>` and :ref:`file mounts with local files <sync-code-artifacts>` will be automatically translated to
  :ref:`SkyPilot Storage <sky-storage>`. Cloud bucket will be created during the job running time, and cleaned up after the job
  finishes.

SkyPilot will launch and start monitoring the spot job. When a preemption happens, SkyPilot will automatically
search for resources across regions and clouds to re-launch the job.

In this example, the job will be restarted from scratch after every recovery.
To resume from the last checkpoint, we need to add a few lines to support checkpointing and resuming.


Checkpointing and resuming
--------------------------

To allow spot recovery, a cloud bucket is typically needed for storing states of the job (e.g., model checkpoints).
Below is an example of mounting a bucket to :code:`/checkpoint`.

.. code-block:: yaml

  file_mounts:
    /checkpoint:
      name: # NOTE: Fill in your bucket name
      mode: MOUNT

The :code:`MOUNT` mode in :ref:`SkyPilot Storage <sky-storage>` ensures the checkpoints outputted to :code:`/checkpoint` are automatically synced to a persistent bucket.
Note that the application code should save program checkpoints periodically and reload those states when the job is restarted.
This is typically achieved by reloading the latest checkpoint at the beginning of your program.

An end-to-end example
---------------------

Below we show an `example <https://github.com/skypilot-org/skypilot/blob/master/examples/spot/bert_qa.yaml>`_ for fine-tuning a bert model on a question answering task with HuggingFace.

.. code-block:: yaml
  :linenos:
  :emphasize-lines: 12-15,42-45

  # bert_qa.yaml
  name: bert_qa

  resources:
    accelerators: V100:1

  # Assume your working directory is under `~/transformers`.
  # To make this example work, please run the following command:
  # git clone https://github.com/huggingface/transformers.git ~/transformers
  workdir: ~/transformers

  file_mounts:
    /checkpoint:
      name: # NOTE: Fill in your bucket name
      mode: MOUNT

  setup: |
    # Fill in your wandb key: copy from https://wandb.ai/authorize
    # Alternatively, you can use `--env WANDB_API_KEY=$WANDB_API_KEY`
    # to pass the key in the command line, during `sky spot launch`.
    echo export WANDB_API_KEY=[YOUR-WANDB-API-KEY] >> ~/.bashrc

    git checkout v4.18.0
    pip install -e .
    cd examples/pytorch/question-answering/
    pip install -r requirements.txt
    pip install wandb

  run: |
    cd ./examples/pytorch/question-answering/
    python run_qa.py \
    --model_name_or_path bert-base-uncased \
    --dataset_name squad \
    --do_train \
    --do_eval \
    --per_device_train_batch_size 12 \
    --learning_rate 3e-5 \
    --num_train_epochs 50 \
    --max_seq_length 384 \
    --doc_stride 128 \
    --report_to wandb \
    --run_name $SKYPILOT_RUN_ID \
    --output_dir /checkpoint/bert_qa/ \
    --save_total_limit 10 \
    --save_steps 1000

As HuggingFace has built-in support for periodically checkpointing, we only need to pass the below arguments for setting up the output directory and frequency of checkpointing (see more on `Huggingface API <https://huggingface.co/docs/transformers/main_classes/trainer#transformers.TrainingArguments.save_steps>`_).

.. code-block:: console

    $ python run_qa.py ... --output_dir /checkpoint/bert_qa/ --save_total_limit 10 --save_steps 1000

.. note::
  You may also refer to another example `here <https://github.com/skypilot-org/skypilot/tree/master/examples/spot/resnet_ddp>`_ for periodically checkpointing with PyTorch.

With the above changes, the managed spot job can now resume training after preemption with ``sky spot launch``! We can enjoy the benefits of
the cost savings from spot instances without worrying about the job being interrupted and losing progress.

.. code-block:: console

    $ sky spot launch -n bert-qa bert_qa.yaml


.. note::
  The environment variable :code:`$SKYPILOT_RUN_ID` can be used to identify the same job, i.e. it is kept identical across all
  recoveries of the job. It can be accessed in the task's :code:`run` commands or directly in the program itself (e.g., access
  via :code:`os.environ` and pass to Weights & Biases for tracking purposes in your training script). It is made available to
  the task whenever it is invoked.


Useful CLIs
-----------

Here are some commands for managed spot jobs. Check :code:`sky spot --help` for more details.

.. code-block:: console

    # Check the status of the spot jobs
    $ sky spot status
    Fetching managed spot job status...
    Managed spot jobs:
    ID NAME     RESOURCES     SUBMITTED   TOT. DURATION   JOB DURATION   #RECOVERIES  STATUS
    2  roberta  1x [A100:8]   2 hrs ago   2h 47m 18s      2h 36m 18s     0            RUNNING
    1  bert-qa  1x [V100:1]   4 hrs ago   4h 24m 26s      4h 17m 54s     0            RUNNING

    # Stream the logs of a running spot job
    $ sky spot logs -n bert-qa

    # Cancel a spot job by name
    $ sky spot cancel -n bert-qa


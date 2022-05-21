.. _spot-jobs:

Managed Spot Jobs
================================================

Sky supports managed spot jobs that can **automatically recover from preemptions**.
This feature **saves significant cost** (e.g., 70\% for GPUs) by making preemptible spot instances practical for long-running jobs.

To launch a spot job, use the same task YAML and invoke it with ``sky spot launch``:

.. code-block:: console

    $ sky spot launch -n bert-qa bert_qa.yaml

Sky will launch and start monitoring the spot job. When a preemption happens, Sky will automatically
attempt to provision the required resources and launch the job again.


The following is an example of a spot job managed by Sky:

.. code-block:: yaml

  # bert_qa.yaml
  name: bert_qa

  resources:
    accelerators: V100:1
    # NOTE: `use_spot` and `spot_recovery` are optional when using `sky spot launch`.
    use_spot: true
    # When a spot cluster is preempted, this strategy recovers by first waiting for
    # the resources in the current region for a while (default: 3 minutes), and
    # then failing over to other regions and clouds, until the resources are launched.
    spot_recovery: FAILOVER

  file_mounts:
    /checkpoint:
      name: # NOTE: Fill in your bucket name
      mode: MOUNT
    /code:
      name: # NOTE: Fill in your bucket name
      # Assume your working directory is under `~/transformers`.
      # To make this example work, please run the following command:
      # git clone https://github.com/huggingface/transformers.git ~/transformers
      source: ~/transformers
      persistent: false
      mode: COPY

  setup: |
    # Fill in your wandb key: copy from https://wandb.ai/authorize
    # Alternatively, you can use `--env WANDB_API_KEY=$WANDB_API_KEY`
    # to pass the key in the command line, during `sky spot launch`.
    echo export WANDB_API_KEY=[YOUR-WANDB-API-KEY] >> ~/.bashrc

    cd /code && git checkout v4.18.0
    pip install -e .
    cd examples/pytorch/question-answering/
    pip install -r requirements.txt
    pip install wandb

  run: |
    cd /code/examples/pytorch/question-answering/
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
    --output_dir /checkpoint/bert_qa/ \
    --report_to wandb \
    --save_total_limit 10

.. note::

  The training code needs to save the training state periodically to a Sky Storage mounted directory
  (:code:`/checkpoint` in the example), and is responsible for reloading the state whenever the job is
  restarted.  This is typically achieved by reloading the latest checkpoint in the directory on program startup.

  :ref:`Workdir <sync-code-artifacts>` and :ref:`file mounts with local files <sync-code-artifacts>` are not
  supported for spot jobs.
  Please use file mounts with cloud storage buckets or :ref:`Sky Storage <sky-storage>` for syncing code and artifacts.

To interact with spot jobs, use ``sky spot status`` and ``sky spot cancel``:

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


Tutorial: DNN Training
======================
In this tutorial, we'll train a Transformer-based language model from HuggingFace.

Defining our Training Task
--------------------------
We'll start by specifying a Sky task YAML with our resource requirements, cluster setup script,
and run command:

.. code-block:: yaml

   # dnn.yaml

   name: huggingface

   resources:
      accelerators:
         V100: 4

   setup: |
      git clone https://github.com/huggingface/transformers/
      cd transformers
      pip3 install .
      cd examples/pytorch/text-classification
      pip3 install -r requirements.txt

   run: |
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


We can launch training by running:

.. code-block:: console

   $ sky launch -c lm-cluster dnn.yaml

This will kick off a single training run after provisioning a cluster. You can safely Ctrl-C after the training job starts printing, the job will be run in the backgroun on the cluster (to stop the job, refer to the `sky cancel` below).

Scheduling Multiple Training Jobs
---------------------------------
What if we would like to run multiple runs on the same cluster scheduled back-to-back
for hyperparameter tuning? This is where Sky's job queue steps in.

We can schedule multiple jobs by using :code:`sky exec`, which will
automatically queue each job on the cluster based on their resource
requirements. The :code:`-d` flag can be used to detach logging from the
terminal, which is useful for launching long-running jobs concurrently.

.. code-block:: bash

   # Launch 4 jobs, perhaps with different hyperparameters.
   sky exec lm-cluster dnn.yaml -d
   sky exec lm-cluster dnn.yaml -d
   # Override task name with `-n` and resource requirement with `--gpus`
   sky exec lm-cluster dnn.yaml -d -n task4 --gpus=V100:2
   sky exec lm-cluster dnn.yaml -d -n task5 --gpus=V100:2

Because the cluster only has 4 V100 GPU, these jobs will be queued waiting for the first job launched by `sky launch`. The last two jobs will be automatically scheduled to run concurrently.

If we wish to view the output for each run after it has completed we can use:

.. code-block:: bash

   # View the jobs in the queue
   sky queue lm-cluster

   ID  NAME         USER  SUBMITTED    STARTED     STATUS   
   5   task5        user  10 mins ago  10 mins ago RUNNING
   4   task4        user  10 mins ago  10 mins ago RUNNING
   3   huggingface  user  10 mins ago  9 mins ago  SUCCEEDED
   2   huggingface  user  10 mins ago  5 mins ago  SUCCEEDED
   1   huggingface  user  10 mins ago  1 min ago   SUCCEEDED


   # Pick a JOB_ID to view
   sky logs lm-cluster JOB_ID

   # Cancel a job
   sky cancel lm-cluster JOB_ID


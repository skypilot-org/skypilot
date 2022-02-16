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
      accelerators: V100

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

This will kick off a single training run after provisioning a cluster.

Scheduling Multiple Training Jobs
---------------------------------
What if we would like to run multiple runs on the same cluster scheduled back-to-back
for hyperparameter tuning? This is where Sky's job queue steps in.

We can schedule multiple jobs by using :code:`sky exec`, which will
automatically queue each job on the cluster based on their resource
requirements. The :code:`-d` flag can be used to detach logging from the
terminal, which is useful for launching long-running jobs concurrently.

.. code-block:: bash

   # Launch 5 jobs, perhaps with different hyperparameters.
   sky exec lm-cluster dnn.yaml -d
   sky exec lm-cluster dnn.yaml -d
   sky exec lm-cluster dnn.yaml -d
   sky exec lm-cluster dnn.yaml -d
   sky exec lm-cluster dnn.yaml -d

Because the cluster has 1 V100 GPU, and each job takes 1 V100 GPU, one job will
start running while the other four will be pending.

If we wish to view the output for each run after it has completed we can use:

.. code-block:: bash

   # View the jobs in the queue
   sky queue lm-cluster

   # Pick a JOB_ID to view
   sky logs lm-cluster JOB_ID

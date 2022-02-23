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
     accelerators: V100:4

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

This will kick off a single training run after provisioning a cluster. You can safely Ctrl-C after the training job starts printing, and the job will continue to run remotely on the cluster. To stop the job, refer to :code:`sky cancel` below.

Scheduling Multiple Training Jobs
---------------------------------
What if we would like to run multiple runs on the same cluster scheduled back-to-back
for hyperparameter tuning? This is where Sky's job queue steps in.

We can schedule multiple jobs by using :code:`sky exec`, which will
automatically queue each job on the cluster based on their resource
requirements. The :code:`-d` flag can be used to detach logging from the
terminal, which is useful for launching long-running jobs concurrently.

.. code-block:: console

   $ # Launch 4 jobs, perhaps with different hyperparameters.
   $ # We can override the task name with `-n` (optional) and resource requirement with `--gpus` (optional)
   $ sky exec lm-cluster dnn.yaml -d -n job2 --gpus=V100:1
   $ sky exec lm-cluster dnn.yaml -d -n job3 --gpus=V100:1
   $ sky exec lm-cluster dnn.yaml -d -n job4 --gpus=V100:3
   $ sky exec lm-cluster dnn.yaml -d -n job5 --gpus=V100:2

Because the cluster only has 4 V100 GPU, we will see the following behavior:

- The :code:`sky launch` job is running and occupies 4 GPU, all other jobs are pending for it.
- The first two :code:`sky exec` jobs (job2, job3) are running and occupy 1 GPU each
- The third job (job4) will be pending, since it requires 3 GPUs and there is only 2 free GPU.
- The last job (job5) will start running, since its requirement is fulfilled with 2 GPUs available.
- Once some of the jobs finished and there are 3 GPUs available, job4 will start running.

If we wish to view the output for each run after it has completed we can use:

.. code-block:: console

   $ # View the jobs in the queue
   $ sky queue lm-cluster

   ID  NAME         USER  SUBMITTED    STARTED     STATUS
   5   job5         user  10 mins ago  10 mins ago RUNNING
   4   job4         user  10 mins ago  -           PENDING
   3   job3         user  10 mins ago  9 mins ago  RUNNING
   2   job2         user  10 mins ago  9 mins ago  RUNNING
   1   huggingface  user  10 mins ago  1 min ago   SUCCEEDED


   $ # Stream the logs of job5 (ID: 5) to the console
   $ sky logs lm-cluster 5

   $ # Cancel job job3 (ID: 3)
   $ sky cancel lm-cluster 3


Transferring Checkpoints and Artifacts
--------------------------------------
To transfer a checkpoint or artifact from the remote VM to the local VM, you can use `scp` or `rsync`:

.. code-block:: console

    $ scp -r local_artifacts/ lm-cluster:/path/to/destination  # copy files to remote VM
    $ scp -r lm-cluster:/path/to/checkpoints local_artifacts/  # copy files from remote VM

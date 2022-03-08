.. _job-queue:
Job Queue
=========

Sky's **job queue** feature allows multiple jobs to be scheduled on a cluster.
This enables parallel experiments or hyperparameter tuning.

Each task submitted by :code:`sky exec` is automatically queued and scheduled
for execution on the cluster. Use the :code:`-d / --detach` flag to detach
logging from the terminal, which is useful for launching many long-running jobs
concurrently.

.. code-block:: bash

   # Launch the job 5 times
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d

To view the output for each job:

.. code-block:: bash

   # Show a cluster's jobs (IDs, statuses).
   sky queue mycluster

   # Stream the outputs of a job.
   sky logs mycluster JOB_ID

To cancel a job:

.. code-block:: bash

   # Cancel a job.
   sky cancel mycluster JOB_ID

   # Cancel all jobs on a cluster.
   sky cancel mycluster --all

Scheduling behavior
--------------------------------

The sky job-queue scheduler is designed to serve two key goals: preventing
resource oversubscription and work-conservation.

1. **Preventing resource oversubscription**: Sky schedules jobs on a cluster using their resource requirements---either specified in a task YAML's :code:`resources` field, or via the :code:`--gpus` option of the :code:`sky exec` CLI command. While honoring these resource requirements, Sky also ensures that no resource in the cluster is over subscribed. I.e., if a node has 4 GPUs, it cannot host a combination of tasks whose sum of GPU requirements exceeds 4.
2. **Work-conservation**: Sky is designed to minimize resource idling. If a resource is idle, sky will schedule a queued job in which can utilize that resource. Resource requirements for tasks are either specified in a task YAML's :code:`resources` field, or via the :code:`--gpus` option of the :code:`sky exec` CLI command.

We illustrate the scheduling behavior by revisiting :ref:`Tutorial: DNN Training <huggingface>`.
In that tutorial, we have a task YAML that specifies these resource requirements:

.. code-block:: yaml

  # dnn.yaml
  ...
  resources:
    accelerators: V100:4
  ...

And we had run the task with:

.. code-block:: console
  sky launch -c lm-cluster dnn.yaml

Since the cluster was created when we ran :code:`sky launch`, sky provisioned
the cluster with exactly the same resources as those required for the task.
Thus, `lm-cluster` has 4 V100 GPUs.

While this initial job is running, let us submit more tasks:

.. code-block:: console

  $ # Launch 4 jobs, perhaps with different hyperparameters.
  $ # You can override the task name with `-n` (optional) and
  $ # the resource requirement with `--gpus` (optional).
  $ sky exec lm-cluster dnn.yaml -d -n job2 --gpus=V100:1
  $ sky exec lm-cluster dnn.yaml -d -n job3 --gpus=V100:1
  $ sky exec lm-cluster dnn.yaml -d -n job4 --gpus=V100:4
  $ sky exec lm-cluster dnn.yaml -d -n job5 --gpus=V100:2

Because the cluster has only 4 V100 GPUs, we will see the following sequence of events:

- The initial :code:`sky launch` job is running and occupies 4 GPUs; all other jobs are pending (no free GPUs).
- The first two :code:`sky exec` jobs (job2, job3) then start running and occupy 1 GPU each.
- The third job (job4) will be pending, since it requires 4 GPUs and there is only 2 free GPUs left.
- The fourth job (job5) will start running, since its requirement is fulfilled with the 2 free GPUs.
- Once all but job5 finish, the cluster's 4 GPUs become free again and job4 will transition from pending to running.

To see job statuses, stream logs, and cancel jobs, use:

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

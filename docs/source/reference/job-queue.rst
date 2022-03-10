.. _job-queue:
Job Queue
=========

Sky's **job queue** allows multiple jobs to be scheduled on a cluster.
This enables parallel experiments or :ref:`hyperparameter tuning <grid-search>`.

Each task submitted by :code:`sky exec` is automatically queued and scheduled
for execution:

.. code-block:: bash

   # Launch the job 5 times.
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d
   sky exec mycluster task.yaml -d

The :code:`-d / --detach` flag detaches logging from the terminal, which is useful for launching many long-running jobs concurrently.

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

Multi-node jobs
--------------------------------

Jobs that run on multiple nodes are also supported by the job queue.

First, create a YAML to specify the desired cluster:

.. code-block:: yaml

  # cluster.yaml
  resources:
    accelerators: V100:8
  num_nodes: 4
  ...

Use :code:`sky launch -c mycluster cluster.yaml` to provision a 4-node (each having 8 V100 GPUs) cluster.
The :code:`num_nodes` field is used to specify how many nodes are required.

Next, use a YAML to specify each task:

.. code-block:: yaml

  # task.yaml
  resources:
    accelerators: V100:8
  num_nodes: 2
  ...

This specifies a task that needs to be run on 2 nodes, each of which must have 8 V100 GPUs.

Use :code:`sky exec mycluster task.yaml` to submit this task, which will be scheduled correctly by the job queue.

See :ref:`Distributed Jobs on Many VMs` for more details.

Scheduling behavior
--------------------------------

Sky's scheduler serves two goals:

1. **Preventing resource oversubscription**: Sky schedules jobs on a cluster
   using their resource requirements---either specified in a task YAML's
   :code:`resources` field, or via the :code:`--gpus` option of the :code:`sky
   exec` CLI command. Sky honors these resource requirements while ensuring that
   no resource in the cluster is oversubscribed. For example, if a node has 4
   GPUs, it cannot host a combination of tasks whose sum of GPU requirements
   exceeds 4.

2. **Minimizing resource idleness**: If a resource is idle, Sky will schedule a
   queued job that can utilize that resource.

We illustrate the scheduling behavior by revisiting :ref:`Tutorial: DNN Training <huggingface>`.
In that tutorial, we have a task YAML that specifies these resource requirements:

.. code-block:: yaml

  # dnn.yaml
  ...
  resources:
    accelerators: V100:4
  ...

Since a new cluster was created when we ran :code:`sky launch -c lm-cluster
dnn.yaml`, Sky provisioned the cluster with exactly the same resources as those
required for the task.  Thus, :code:`lm-cluster` has 4 V100 GPUs.

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

Thus, we may see the following job statuses on this cluster:

.. code-block:: console

  $ sky queue lm-cluster

   ID  NAME         USER  SUBMITTED    STARTED     STATUS
   5   job5         user  10 mins ago  10 mins ago RUNNING
   4   job4         user  10 mins ago  -           PENDING
   3   job3         user  10 mins ago  9 mins ago  RUNNING
   2   job2         user  10 mins ago  9 mins ago  RUNNING
   1   huggingface  user  10 mins ago  1 min ago   SUCCEEDED

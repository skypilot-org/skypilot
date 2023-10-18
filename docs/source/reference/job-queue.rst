.. _job-queue:

Job Queue
=========

SkyPilot's **job queue** allows multiple jobs to be scheduled on a cluster.

Getting started
--------------------------------

Each task submitted by :code:`sky exec` is automatically queued and scheduled
for execution on an existing cluster:

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

First, create a :code:`cluster.yaml` to specify the desired cluster:

.. code-block:: yaml

  num_nodes: 4
  resources:
    accelerators: V100:8

  workdir: ...
  setup: |
    # Install dependencies.
    ...

Use :code:`sky launch -c mycluster cluster.yaml` to provision a 4-node (each having 8 V100 GPUs) cluster.
The :code:`num_nodes` field is used to specify how many nodes are required.

Next, create a :code:`task.yaml` to specify each task:

.. code-block:: yaml

  num_nodes: 2
  resources:
    accelerators: V100:4

  run: |
    # Run training script.
    ...

This specifies a task that needs to be run on 2 nodes, each of which must have 4 free V100s.

Use :code:`sky exec mycluster task.yaml` to submit this task, which will be scheduled correctly by the job queue.

See :ref:`dist-jobs` for more details.

Using ``CUDA_VISIBLE_DEVICES``
--------------------------------

The environment variable ``CUDA_VISIBLE_DEVICES`` will be automatically set to
the devices allocated to each task on each node. This variable is set
when a task's ``run`` commands are invoked.

For example, ``task.yaml`` above launches a 4-GPU task on each node that has 8
GPUs, so the task's ``run`` commands will be invoked with
``CUDA_VISIBLE_DEVICES`` populated with 4 device IDs.

If your ``run`` commands use Docker/``docker run``, simply pass ``--gpus=all``;
the correct environment variable would be set inside the container (only the
allocated device IDs will be set).

Example: Grid Search
----------------------

To submit multiple trials with different hyperparameters to a cluster:

.. code-block:: bash

  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-3
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 3e-3
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-4
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-2
  $ sky exec mycluster --gpus V100:1 -d -- python train.py --lr 1e-6

Options used:

- :code:`--gpus`: specify the resource requirement for each job.
- :code:`-d` / :code:`--detach`: detach the run and logging from the terminal, allowing multiple trials to run concurrently.

If there are only 4 V100 GPUs on the cluster, SkyPilot will queue 1 job while the
other 4 run in parallel. Once a job finishes, the next job will begin executing
immediately.
See :ref:`below <scheduling-behavior>` for more details on SkyPilot's scheduling behavior.

.. tip::

  You can also use :ref:`environment variables <env-vars>` to set different arguments for each trial.

Example: Fractional GPUs
-------------------------

To run multiple trials per GPU, use *fractional GPUs* in the resource requirement.
For example, use :code:`--gpus V100:0.5` to make 2 trials share 1 GPU:

.. code-block:: bash

  $ sky exec mycluster --gpus V100:0.5 -d -- python train.py --lr 1e-3
  $ sky exec mycluster --gpus V100:0.5 -d -- python train.py --lr 3e-3
  ...

When sharing a GPU, ensure that the GPU's memory is not oversubscribed
(otherwise, out-of-memory errors could occur).

.. _scheduling-behavior:

Scheduling behavior
--------------------------------

SkyPilot's scheduler serves two goals:

1. **Preventing resource oversubscription**: SkyPilot schedules jobs on a cluster
   using their resource requirements---either specified in a task YAML's
   :code:`resources` field, or via the :code:`--gpus` option of the :code:`sky
   exec` CLI command. SkyPilot honors these resource requirements while ensuring that
   no resource in the cluster is oversubscribed. For example, if a node has 4
   GPUs, it cannot host a combination of tasks whose sum of GPU requirements
   exceeds 4.

2. **Minimizing resource idleness**: If a resource is idle, SkyPilot will schedule a
   queued job that can utilize that resource.

We illustrate the scheduling behavior by revisiting :ref:`Tutorial: DNN Training <dnn-training>`.
In that tutorial, we have a task YAML that specifies these resource requirements:

.. code-block:: yaml

  # dnn.yaml
  ...
  resources:
    accelerators: V100:4
  ...

Since a new cluster was created when we ran :code:`sky launch -c lm-cluster
dnn.yaml`, SkyPilot provisioned the cluster with exactly the same resources as those
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

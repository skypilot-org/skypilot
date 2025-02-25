.. _job-priorities:

Managed Jobs with Priorities
============================

SkyPilot supports priority-based scheduling, preemption, and re-queuing of jobs running on Kubernetes. You can achieve this by leveraging Kubernetes' native priority classes.

.. tip::
   Jobs with priorities and preemption are only supported on Kubernetes.

To set job priorities:

1. Create :ref:`priority classes <priorities-example-priority-classes>` in your Kubernetes cluster.
2. :ref:`Set the priority classes in your SkyPilot jobs<priorities-example-sky-pilot-jobs>` by setting ``experimental.config_overrides.kubernetes.pod_config.spec.priorityClassName``.
3. Use :ref:`sky jobs launch <managed-jobs>` to launch your jobs.

With this setup, you can run high priority jobs that preempt low priority jobs when resources are constrained.

How Priorities and Preemptions Work
-----------------------------------

When the cluster does not have enough resources to run all jobs, **high priority jobs will preempt low priority jobs.** This means pods of low priority jobs will be terminated to create space for high priority jobs.

Preempted jobs will be automatically rescheduled by SkyPilot when resources become available again. You can set up :ref:`checkpointing and recovery <checkpointing>` in your code to reduce wasted work.

Jobs with the same priority level follow SkyPilot's :ref:`default scheduling behavior <job-queue>`.

.. tip::
   You can also apply priority classes to unmanaged SkyPilot clusters. However, when unmanaged clusters are preempted, they will not be automatically restarted.

Limitations
-----------

..
  TODO: The first point is a very severe limitation. We need to fix this and do a better job of explaining it.

1. When the job queue is longer than 4x the number of CPUs on the SkyPilot controller, any new high priority jobs will not be able to preempt low priority jobs until they are at the front of the queue.
2. Priority settings only apply within a Kubernetes cluster.
3. Preemption behavior depends on your cluster's configuration and may preempt other pods in the cluster.

For more information, refer to the `Kubernetes documentation on Pod Priority and Preemption <https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/>`_.

.. _priorities-example:

Working Example
---------------

Below we show an example run with two priority classes: ``high-priority`` and ``low-priority``.

.. _priorities-example-priority-classes:

Step 1: Create Priority Classes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create two `priority classes <https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/#priorityclass>`_ in your Kubernetes cluster:

.. code-block:: yaml

  # priorities.yaml
  apiVersion: scheduling.k8s.io/v1
  kind: PriorityClass
  metadata:
    name: high-priority
  value: 1000000
  globalDefault: false
  description: "High priority class for critical jobs"
  ---
  apiVersion: scheduling.k8s.io/v1
  kind: PriorityClass
  metadata:
    name: low-priority
  value: 10000
  globalDefault: true
  description: "Low priority class for background jobs"

A higher value indicates higher priority. You can create as many priority classes as you want.

Apply these priority classes to your cluster:

.. code-block:: bash

  kubectl apply -f priorities.yaml

.. _priorities-example-sky-pilot-jobs:

Step 2: Setting Priorities in SkyPilot Jobs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To assign priorities to your SkyPilot tasks, use the ``experimental.config_overrides.kubernetes.pod_config`` field in your task YAML. 

We use two simple counter jobs in this example:

.. code-block:: yaml

  # high-priority-task.yaml
  resources:
    cloud: kubernetes
    cpus: 4

  run: |
    python -c '
    import time
    for i in range(1000):
        print(f"High priority counter: {i}")
        time.sleep(1)
    '

  experimental:
    config_overrides:
      kubernetes:
        pod_config:
          spec:
            priorityClassName: high-priority

.. code-block:: yaml

  # low-priority-task.yaml
  resources:
    cloud: kubernetes
    cpus: 4

  run: |
    python -c '
    import time
    for i in range(1000):
        print(f"Low priority counter: {i}")
        time.sleep(1)
    '

  experimental:
    config_overrides:
      kubernetes:
        pod_config:
          spec:
            priorityClassName: low-priority

.. tip::
   For this example, be sure to set the ``resources.cpu`` field such that once one job is running, there are no CPUs left for the other job in the cluster.

Step 3: Launch Your Jobs
~~~~~~~~~~~~~~~~~~~~~~~~

Use ``sky jobs launch`` to launch your jobs as managed jobs. First, we launch the low priority job:

.. code-block:: bash

  sky jobs launch low-priority-task.yaml

Then launch the high priority job:

.. code-block:: bash

  sky jobs launch high-priority-task.yaml

Use ``sky jobs queue`` to see the status of your jobs. You will see that the high priority job starts running immediately and the low priority job is preempted.

The low priority job will be in ``RECOVERING`` state. SkyPilot will automatically restart the low priority job when resources become available.

.. code-block:: bash

  $ sky jobs queue
  Fetching managed job statuses...
  Managed jobs
  In progress tasks: 1 RECOVERING, 1 RUNNING
  ID  TASK  NAME             RESOURCES  SUBMITTED   TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
  2   -     sky-0232-romilb  1x[CPU:4]  5 mins ago  5m 35s         5m 4s         0            RUNNING
  1   -     sky-0d6f-romilb  1x[CPU:4]  7 mins ago  7m 13s         1m 34s        0            RECOVERING

Once the high priority job finishes, the low priority job will start running again.

..
    TODO: Add a nice graphic here?

.. code-block:: bash

  $ sky jobs queue
  Fetching managed job statuses...
  Managed jobs
  No in-progress managed jobs.
  ID  TASK  NAME             RESOURCES  SUBMITTED    TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
  2   -     sky-0232-romilb  1x[CPU:4]  23 mins ago  17m 22s        16m 51s       0            SUCCEEDED
  1   -     sky-0d6f-romilb  1x[CPU:4]  25 mins ago  23m 47s        18m 25s       1            RUNNING 
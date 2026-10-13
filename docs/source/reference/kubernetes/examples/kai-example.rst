.. _kubernetes-example-kai:

Using SkyPilot with KAI Scheduler
=================================

`KAI Scheduler <https://github.com/kai-scheduler/KAI-Scheduler>`_ is a
Kubernetes scheduler with hierarchical queues, quotas and gang scheduling.
SkyPilot can submit each cluster to a KAI queue as a gang, so all of its pods
are scheduled together.

Prerequisites
-------------

* KAI Scheduler v0.15 or later.
* A KAI ``Queue`` to submit to.
* Permission to get, create, patch and delete ``podgroups.scheduling.run.ai``
  in the namespace SkyPilot launches into, and to get
  ``queues.scheduling.run.ai``.

Configuration
-------------

Set the queue in ``~/.sky/config.yaml``, per context or for all contexts:

.. code-block:: yaml

    kubernetes:
      context_configs:
        my-context:
          kai:
            queue: team-a
            preemptibility: preemptible  # Optional.

It can also be set per task under ``config.kubernetes.kai``. To set the pods'
``PriorityClass``, use ``resources.priority_class``.

Behavior
--------

* SkyPilot creates a ``PodGroup`` for the cluster before its pods, with
  ``minMember`` set to the number of nodes, and deletes it on ``sky down``.
* While KAI keeps the cluster queued, the launch request waits on the API
  server without holding a worker, and resumes once KAI schedules it.
* If the cluster exceeds a GPU limit of its queue or any parent queue, or the
  queue does not exist, the launch fails.
* Cancelling a queued launch leaves the cluster's pods in the queue. Run
  ``sky down`` to remove them.
* The ``PodGroup`` has the same labels as the pods, so labels set with
  :ref:`custom_metadata <config-yaml-kubernetes-custom-metadata>` apply to both.

Limitations
-----------

* KAI and :ref:`Kueue <kubernetes-example-kueue>` cannot be used for the same
  cluster.
* High availability controllers are not supported.

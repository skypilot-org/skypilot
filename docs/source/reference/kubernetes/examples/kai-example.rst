.. _kubernetes-example-kai:

Using SkyPilot with KAI Scheduler
=================================

`KAI Scheduler <https://github.com/kai-scheduler/KAI-Scheduler>`_ is a
Kubernetes scheduler with hierarchical queues, quotas and gang scheduling.
SkyPilot can submit each cluster to a KAI queue as a gang, so all of its pods
are scheduled together.

Prerequisites
-------------

* KAI Scheduler, with its default scheduler name ``kai-scheduler``.
* A leaf KAI ``Queue`` to submit to. KAI never schedules a ``PodGroup`` in a
  parent queue, so a launch into one waits until it times out.
* Permission to get, create and delete ``podgroups.scheduling.run.ai``
  in the namespace SkyPilot launches into, and to get
  ``queues.scheduling.run.ai``. Queues are cluster-scoped, so the second
  needs a ``ClusterRole``, as does getting ``topologies.kai.scheduler`` if you
  set a topology.

Setting a queue
---------------

Set the queue in the API server's ``~/.sky/config.yaml``, for one context or
for all contexts:

.. code-block:: yaml

    kubernetes:
      context_configs:
        my-context:
          kai:
            queue: team-a
            preemptibility: preemptible  # Optional.

A task can also set the queue under ``config.kubernetes.kai.queue``. It wins
over a queue set for all contexts. It loses to one set for its context, unless
its workspace has an entry under ``workspaces``. To fix a team's queue, set it
per workspace, as below.

Giving each team its own queue
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Set the queue for a context in each :ref:`workspace <workspaces>`. A task
cannot override it, so the workspace's members launch into that queue:

.. code-block:: yaml

    workspaces:
      team-a:
        kubernetes:
          context_configs:
            my-context:
              kai:
                queue: team-a

This chooses the queue SkyPilot submits to. It does not restrict access to
other queues: a task's ``pod_config`` can still change the pods' scheduling
fields. To enforce queue access, use a policy on the cluster or an
:ref:`admin policy <advanced-policy-config>`.

Scheduling shards
~~~~~~~~~~~~~~~~~

If your KAI installation runs scheduling shards, give the cluster the shard's
label with :ref:`custom_metadata <config-yaml-kubernetes-custom-metadata>`.

* A shard only schedules ``PodGroup`` objects that carry its label.
* KAI's default shard only schedules ``PodGroup`` objects without a shard
  label.
* The queue must be in the same shard.

A ``PodGroup`` that no shard selects is never evaluated. Its launch waits for
the full :ref:`provision_timeout <config-yaml-kubernetes-provision-timeout>`,
which is 24 hours by default with a KAI queue.

Keeping pods close together
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To keep a cluster's pods close together in the network, name a KAI
``Topology`` and one of its levels:

.. code-block:: yaml

    kubernetes:
      context_configs:
        my-context:
          kai:
            queue: team-a
            topology: fabric
            preferred_topology_level: leaf

* ``preferred_topology_level``: KAI places all of the cluster's pods in one
  domain at that level if it can. Otherwise it uses one domain at the lowest
  broader level that has room, up to the whole cluster.
* ``required_topology_level``: KAI places all of the cluster's pods in one
  domain at that level, and the cluster stays queued until a domain has room.
  KAI does not tell a full domain apart from one too small for the cluster, so
  a cluster larger than every domain at that level stays queued until you
  cancel it.

Name a level by its node label or alias. The preferred level must be at or
below the required one. SkyPilot checks both against the ``Topology`` before
it creates the ``PodGroup``. A cluster with a topology only uses nodes that
carry a label for every level of the ``Topology``.

How a launch works
------------------

1. SkyPilot creates a ``PodGroup`` for the cluster before its pods, with
   ``minMember`` set to the number of nodes. The ``PodGroup`` has the same
   labels as the pods, so labels set with
   :ref:`custom_metadata <config-yaml-kubernetes-custom-metadata>` apply to
   both.
2. While KAI keeps the cluster queued, the launch request waits on the API
   server without holding a worker. It prints KAI's reason for keeping the
   cluster queued, and resumes once KAI schedules it.
3. If the cluster asks for more GPUs than the limit of its queue or a parent
   queue, asks for more than the quota as a non-preemptible cluster, or the
   queue does not exist, the launch fails over to the next allowed context,
   if any. Other resources over a limit keep the launch queued instead.
4. ``sky down`` deletes the cluster's pods and its ``PodGroup``.

Relaunching onto a running cluster cannot change its queue, priority class,
preemptibility or topology. Run ``sky down`` first.

Jobs and serve controllers are not submitted to KAI, so their pods are not
subject to queue limits or preemption.

Priority and preemption
-----------------------

KAI orders and preempts the gang by the ``PodGroup``'s ``PriorityClass``.
SkyPilot sets it from ``resources.priority_class``, on the ``PodGroup`` and on
the pods, so the ``PriorityClass`` must exist. Unless
:ref:`kubernetes.kai.preemptibility <config-yaml-kubernetes-kai-preemptibility>`
is set, a priority below 100 is preemptible.

Eviction and recovery
---------------------

* When KAI evicts a gang, by reclaim, preemption or consolidation, it deletes
  the cluster's pods. ``sky status --refresh`` then removes the cluster, and a
  :ref:`managed job <managed-jobs>` recovers onto a new one.
* A pod that SkyPilot finds while KAI deletes it is reported as evicted by
  KAI, with KAI's reason.
* If a pod of a running cluster fails, KAI evicts the rest of the gang after
  its staleness grace period, 60 seconds by default.
* Cancelling a ``sky launch`` that KAI keeps queued leaves the cluster's pods
  in KAI's queue, where KAI can still schedule them. Run ``sky down`` to remove
  them. Cancelling a managed job removes them.

Troubleshooting
---------------

* ``kubectl describe podgroup <name>`` shows KAI's full scheduling conditions.
  The ``PodGroup`` is named like the cluster's pods, without the ``-head``
  suffix.
* A queue's GPU ``limit`` of ``-1`` is unlimited. An unset limit is 0, so a
  GPU launch into that queue fails.

Limitations
-----------

* KAI and :ref:`Kueue <kubernetes-example-kueue>` cannot be used for the same
  cluster.

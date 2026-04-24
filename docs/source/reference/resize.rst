.. _cluster-resize:

Resizing a Cluster
==================

SkyPilot supports **resizing** an existing cluster up or down to a different
number of nodes, without having to tear it down and relaunch.

Use ``sky launch --resize`` with ``-c``/``--cluster`` and ``--num-nodes`` to
set the new target node count:

.. code-block:: bash

    # Scale up: add more worker nodes.
    $ sky launch -c my-cluster --resize --num-nodes 8

    # Scale down: remove excess worker nodes.
    $ sky launch -c my-cluster --resize --num-nodes 2

Before proceeding, SkyPilot shows a confirmation prompt with the current and
target node counts, for example:

.. code-block:: text

    Resizing cluster 'my-cluster' from 4 to 8 node(s) (+4 worker(s), scale up). Proceed? [Y/n]

When to use
-----------

- **Scale up** during the life of a training job when you need more compute,
  without losing the cluster's on-disk state or re-running ``setup``.
- **Scale down** to save cost between experiments while keeping the head node
  and its state (job queue, logs, setup, caches) intact.

Behavior
--------

**Scale up.** New worker nodes are provisioned via the same provisioning path
as ``sky launch``. Existing nodes are preserved. Your task's ``setup`` is
re-run on the newly added workers.

**Scale down.** SkyPilot verifies that no jobs are ``RUNNING``,
``SETTING_UP``, or ``PENDING`` on the cluster, then terminates all worker
nodes and re-provisions only the workers needed for the new target count. The
head node is always preserved, so the cluster's job queue, logs, and setup
state survive the resize.

.. note::

  Scale-down re-provisions all workers from scratch rather than selectively
  terminating excess ones. This is cloud-agnostic but slightly wasteful for
  small scale-downs (e.g. 10 → 9 still re-provisions 9 workers).

Requirements and restrictions
-----------------------------

- ``--resize`` requires ``-c/--cluster`` to identify an existing cluster.
- The cluster must be in the ``UP`` state (not ``STOPPED`` or ``INIT``).
  Start the cluster first with ``sky start`` if it is stopped.
- **Scale-down** is rejected if any job is still ``RUNNING``,
  ``SETTING_UP``, or ``PENDING``. Cancel those jobs first with
  ``sky cancel <cluster> -a``.
- During a resize, only ``--num-nodes`` may change. Other hardware
  specifications (instance type, accelerators, region, zone, ports, etc.)
  must match the existing cluster. If you specify a different hardware
  spec (e.g. a different GPU type), the resize is rejected.
- ``--resize`` against a non-existent cluster is ignored with a warning and
  falls through to a regular ``sky launch`` that creates the cluster.

Examples
--------

Resize from YAML (``num_nodes`` in the YAML is used as the target):

.. code-block:: bash

    $ sky launch -c my-cluster --resize cluster.yaml

Scale down an idle cluster to a single node to save cost between runs:

.. code-block:: bash

    $ sky launch -c my-cluster --resize --num-nodes 1

Resize non-interactively (e.g. from scripts or CI):

.. code-block:: bash

    $ sky launch -y -c my-cluster --resize --num-nodes 4

Python SDK
----------

The same behavior is available programmatically via ``sky.launch(resize=True)``:

.. code-block:: python

    import sky

    task = sky.Task(run='echo hi')
    task.num_nodes = 8

    request_id = sky.launch(task, cluster_name='my-cluster', resize=True)
    job_id, handle = sky.stream_and_get(request_id)

Backward compatibility
----------------------

``resize=True`` requires API server version ``>= 50``. Passing ``resize=True``
against an older API server raises ``sky.exceptions.APINotSupportedError``
with an upgrade message.

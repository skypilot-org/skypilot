.. _auto-stop:

Autostop and Autodown
============================

The **autostop** (or **autodown**) feature automatically stops (or tears down) a
cluster after it becomes :ref:`idle <auto-stop-setting-idleness-behavior>`.

With autostop, users can simply submit jobs and leave their laptops, while
ensuring no unnecessary spending occurs. After jobs have finished, the
clusters used will be automatically stopped, which can be restarted later.

With autodown, the clusters used will be automatically torn down, i.e.,
terminated.

.. note::

  The autostop/autodown logic is executed by the remote cluster.  Your local
  machine does *not* need to stay up for them to take effect.

Setting autostop
~~~~~~~~~~~~~~~~

To schedule autostop for a cluster, set autostop in the SkyPilot YAML:

.. code-block:: yaml

   resources:
     autostop: true  # Stop after default idle minutes (5).

     # Or:
     autostop: 10m  # Stop after this many idle minutes.

     # Or:
     autostop:
       idle_minutes: 10

Alternatively, use :code:`sky autostop` or ``sky launch -i <idle minutes>``:

.. code-block:: bash

   # Launch a cluster with logging detached (the -d flag)
   sky launch -d -c mycluster cluster.yaml

   # Autostop the cluster after 10 minutes of idleness
   sky autostop mycluster -i 10

   # Use the default, 5 minutes of idleness
   # sky autostop mycluster

   # (Equivalent to the above) Use the -i flag:
   sky launch -d -c mycluster cluster.yaml -i 10


Setting autodown
~~~~~~~~~~~~~~~~

To schedule autodown for a cluster, set autodown in the SkyPilot YAML:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 10
       down: true  # Use autodown.

Alternatively, pass the ``--down`` flag to either :code:`sky autostop` or ``sky launch``:

.. code-block:: bash

   # Add the --down flag to schedule autodown instead of autostop.

   # This means the cluster will be torn down after 10 minutes of idleness.
   sky launch -d -c mycluster2 cluster.yaml -i 10 --down

   # Or:
   sky autostop mycluster2 -i 10 --down


Canceling autostop/autodown
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To cancel any scheduled autostop/autodown on the cluster:

.. code-block:: bash

   sky autostop mycluster --cancel

Viewing autostop status
~~~~~~~~~~~~~~~~~~~~~~~

To view the status of the cluster, use ``sky dashboard`` or ``sky status``:

.. code-block:: bash

   $ sky status
   NAME         INFRA           RESOURCES                     STATUS   AUTOSTOP       LAUNCHED
   mycluster    AWS (us-east-1) 2x(cpus=8, m4.2xlarge, ...)   UP       10 min         1 min ago
   mycluster2   AWS (us-east-1) 2x(cpus=8, m4.2xlarge, ...)   UP       10 min(down)   1 min ago

Clusters that are autostopped/autodowned are automatically removed from the status table.

.. _auto-stop-setting-idleness-behavior:

Setting idleness behavior
~~~~~~~~~~~~~~~~~~~~~~~~~~

A cluster is considered idle if there are no in‑progress jobs (pending or running)
and no active SSH sessions. You can change the idleness criteria in SkyPilot YAML with
:ref:`resources.autostop.wait_for <yaml-spec-resources-autostop>`.

``wait_for`` can be set as one of the followings:

- ``jobs_and_ssh`` (default): Wait for in‑progress jobs and SSH connections to finish.
- ``jobs``: Only wait for in‑progress jobs — useful for ignoring long‑running SSH or IDE connections.
- ``none``: Wait for nothing; autostop right after ``idle_minutes`` — useful for ignoring long‑running jobs (e.g., Jupyter notebooks) and enforcing a hard time limit.

Examples:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 10
       wait_for: jobs_and_ssh

Alternatively, pass the ``--wait-for`` flag to either ``sky autostop`` or ``sky launch``:

.. code-block:: bash

   # Default: Running jobs and active SSH sessions reset the idleness timer.
   sky launch -d -c mycluster cluster.yaml -i 10 --wait-for jobs_and_ssh

   # Or:
   sky autostop mycluster -i 10 --wait-for jobs_and_ssh

   # Only running jobs reset the idleness timer.
   sky autostop mycluster -i 10 --wait-for jobs

   # Hard time limit: Stop after 10 minutes, regardless of running jobs or SSH sessions.
   sky autostop mycluster -i 10 --wait-for none

Running scripts before autostop
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To run a script on the cluster before autostop fires (for example, to commit
code, sync W&B runs, or push checkpoints to persistent storage), use a task
:ref:`lifecycle hook <lifecycle-hooks>` with ``events: [stop]`` (or
``events: [down]`` if you use ``autostop: {down: true}`` / autodown, since
the outcome is teardown). The same mechanism also covers preemption and
``sky down`` events.

.. _auto-stop-idle-detection:

Detecting idle clusters from metrics
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Autostop runs on the cluster and only sees SkyPilot jobs and SSH sessions, so a cluster with a hung job or an idle notebook never looks idle to it. Administrators who already collect metrics (GPU, CPU, or anything else) can have the API server use them instead:

.. code-block:: yaml

  # API server config
  idle_detection:
    window: 6h
    source:
      prometheus:
        url: https://prometheus.example.com/api/v1/query
        query: avg_over_time(DCGM_FI_DEV_GPU_UTIL{pod=~"{cluster_on_cloud}-.*"}[{window}])
        threshold: 5
    notify:
      webhook: https://example.com/idle-clusters

Every 15 minutes the API server sends the query for each ``UP`` cluster. A cluster whose result stays below ``threshold`` for the whole ``window`` gets an event in ``sky status -v`` and a ``POST`` to the webhook naming the cluster and its owner, repeated once per ``window`` while it stays idle. A second event marks the end of the idle period. Clusters are not stopped.

SkyPilot does not collect the metrics or know what they mean: the query is yours, and a source that is not PromQL-compatible can be put behind ``source.http``. See :ref:`idle_detection <config-yaml-idle-detection>` for all fields.

- A cluster that cannot be measured (no data, an error from the source) counts as active.
- The query decides how much data is enough. ``avg_over_time`` averages whatever samples exist; see :ref:`idle_detection <config-yaml-idle-detection>` for requiring data from the start of the window.
- A cluster is first checked after the API server has seen it ``UP`` for a full ``window``, so nothing is reported for the first ``window`` after turning this on, or after ``sky start``.
- Running jobs and SSH sessions do not exempt a cluster; only the source decides.
- Controllers and clusters launched by managed jobs, services or pools are never checked.
- To exempt a cluster, launch it with the label ``skypilot-idle-detection: skip`` under ``resources.labels``. Use an :ref:`admin policy <advanced-policy-config>` to require or forbid that label.

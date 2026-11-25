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

.. _auto-stop-activity-check:

Custom activity check
~~~~~~~~~~~~~~~~~~~~~

Work started outside SkyPilot jobs (for example with ``nohup`` or ``tmux``
over SSH) does not reset the idleness timer, so the cluster can stop while
it is still busy. To add your own definition of activity, set
:ref:`resources.autostop.activity_check <yaml-spec-resources-autostop>`.
While the cluster has no jobs (and, with ``jobs_and_ssh``, no SSH sessions),
SkyPilot runs the command on the head node every minute:

- exit code ``0``: the cluster is active, and the idleness timer resets.
- exit code ``1``: the cluster is idle.
- any other exit code, a timeout or a failure to run: treated as active, so
  a broken check never stops the cluster.

For example, to keep a cluster up while any GPU averages at least 10%
utilization over the last few seconds:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 30
       activity_check:
         run: |
           nvidia-smi -q -d UTILIZATION | awk '
             /GPU Utilization Samples/ {s = 1; seen = 1}
             s && /Avg/ {if ($3 >= 10) busy = 1; s = 0}
             END {exit seen ? !busy : 2}'
         timeout: 30  # Seconds; default 10, at most 60.

The check runs only on the head node and is skipped with ``wait_for: none``.
It is kept by ``sky autostop`` and ``sky start``. Like
:ref:`lifecycle hooks <lifecycle-hooks>`, it follows the latest launch: a
``sky launch`` on the cluster that sets autostop (in YAML or with ``-i``)
replaces the check with that launch's ``activity_check``, or removes it if
there is none.
Administrators can require one for every cluster with an
:ref:`admin policy <advanced-policy-config>`.

Running scripts before autostop
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To run a script on the cluster before autostop fires (for example, to commit
code, sync W&B runs, or push checkpoints to persistent storage), use a task
:ref:`lifecycle hook <lifecycle-hooks>` with ``events: [stop]`` (or
``events: [down]`` if you use ``autostop: {down: true}`` / autodown, since
the outcome is teardown). The same mechanism also covers preemption and
``sky down`` events.

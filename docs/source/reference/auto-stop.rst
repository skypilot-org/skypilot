.. _auto-stop:

Autostop and Autodown
============================

The **autostop** (or **autodown**) feature automatically stops (or tears down) a
cluster after it becomes idle. By default, idleness means there are no
in‑progress (pending/running) jobs in the cluster’s job queue and no active SSH
sessions; both running jobs and SSH sessions reset the idleness timer (see
"Setting idleness behavior" below).

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

Setting idleness behavior
~~~~~~~~~~~~~~~~~~~~~~~~~~

To control when the idleness timer resets, set the wait mode in the SkyPilot YAML:

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

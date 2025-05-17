.. _auto-stop:

Autostop and Autodown
============================

The **autostop** (or **autodown**) feature automatically stops (or tears down) a
cluster after it becomes idle.

With autostop, users can simply submit jobs and leave their laptops, while
**ensuring no unnecessary spending occurs**: after jobs have finished, the
cluster(s) used will be automatically stopped (which can be restarted later).

With autodown, the cluster(s) used will be automatically torn down (i.e.,
terminated) instead.

To schedule autostop for a cluster, use :code:`sky autostop` or ``sky launch -i <idle minutes>``:

.. code-block:: bash

   # Launch a cluster with logging detached (the -d flag)
   sky launch -d -c mycluster cluster.yaml

   # Autostop the cluster after 10 minutes of idleness
   sky autostop mycluster -i 10

   # Use the default, 5 minutes of idleness
   # sky autostop mycluster

   # (Equivalent to the above) Use the -i flag:
   sky launch -d -c mycluster cluster.yaml -i 10

To schedule autodown for a cluster, pass the ``--down`` flag to either :code:`sky autostop` or ``sky launch``:

.. code-block:: bash

   # Add the --down flag to schedule autodown instead of autostop.

   # This means the cluster will be torn down after 10 minutes of idleness.
   sky launch -d -c mycluster2 cluster.yaml -i 10 --down

   # Or:
   sky autostop mycluster2 -i 10 --down

.. note::

  The autostop/autodown logic will be automatically executed by the remote
  cluster.  Your local machine does *not* need to stay up for them to take
  effect.

To cancel any scheduled autostop/autodown on the cluster:

.. code-block:: bash

   sky autostop mycluster --cancel

To view the status of the cluster, use ``sky status [--refresh]``:

.. code-block:: bash

   $ sky status
   NAME         INFRA           RESOURCES                     STATUS   AUTOSTOP       LAUNCHED
   mycluster    AWS (us-east-1) 2x(cpus=8, type=m4.2xlarge)   UP       10 min         1 min ago
   mycluster2   AWS (us-east-1) 2x(cpus=8, type=m4.2xlarge)   UP       10 min(down)   1 min ago

   # Refresh the statuses by querying the cloud providers
   $ sky status --refresh
   I 06-27 13:36:11 backend_utils.py:2273] Autodowned cluster: mycluster2
   NAME         INFRA           RESOURCES                     STATUS   AUTOSTOP  LAUNCHED
   mycluster    AWS (us-east-1) 2x(cpus=8, type=m4.2xlarge)   STOPPED  10 min    11 min ago

Note that :code:`sky status` shows the cached statuses, which can be outdated for clusters with autostop/autodown scheduled.
To query the latest statuses of those clusters, use :code:`sky status --refresh`.

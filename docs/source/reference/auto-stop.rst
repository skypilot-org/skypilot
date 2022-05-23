Auto-stopping
==============

Sky's **auto-stopping** automatically stops a cluster after it becomes idle.

With auto-stopping, users can simply submit jobs and leave their laptops, while
**ensuring no unnecessary spending occurs**: after jobs have finished, the
cluster(s) used will be automatically stopped (which can be restarted later).

To schedule auto-stopping for a cluster, use :code:`sky autostop`:

.. code-block:: bash

   # Launch a cluster with logging detached
   sky launch -d -c mycluster cluster.yaml

   # Auto-stop the cluster after 10 minutes of idleness
   sky autostop mycluster -i 10

   # Use the default, 5 minutes of idleness
   # sky autostop mycluster

The :code:`-d / --detach` flag detaches logging from the terminal.

To cancel a scheduled auto-stop on the cluster:

.. code-block:: bash

   sky autostop mycluster --cancel

To view the status of the cluster, use ``sky status [--refresh]``:

.. code-block:: bash

   # Show a cluster's jobs (IDs, statuses).
   sky status
   NAME         LAUNCHED    RESOURCES            STATUS   AUTOSTOP  COMMAND
   mycluster    1 min ago   2x AWS(m4.2xlarge)   UP       10 min    sky launch -d -c ...

   # Refresh the status for auto-stopping
   sky status --refresh
   NAME         LAUNCHED    RESOURCES            STATUS   AUTOSTOP  COMMAND
   mycluster    11 min ago  2x AWS(m4.2xlarge)   STOPPED  10 min    sky launch -d -c ...


:code:`sky status` shows the cached statuses, which can be outdated for clusters with auto-stopping scheduled. To query the real statuses of clusters with auto-stopping scheduled, use :code:`sky status --refresh`.

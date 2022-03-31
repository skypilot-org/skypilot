.. _job-queue:
Auto-stopping
=========

Sky's **auto-stopping** can automatically stop a cluster after a few minutes of idleness.

To setup auto-stopping for a cluster, :code:`sky autostop` can be used.

.. code-block:: bash

   # Launch a cluster with logging detached
   sky launch -c mycluster -d cluster.yaml

   # Set auto-stopping for the cluster, after cluster will be stopped 10 minutes of idleness
   sky autostop mycluster -i 10

The :code:`-d / --detach` flag detaches logging from the terminal.

To cancel the auto-stop scheduled on the cluster:

.. code-block:: bash

   # Cancel auto-stop for the cluster
   sky autostop mycluster --cancel

To view the status of the cluster:

.. code-block:: bash

   # Show a cluster's jobs (IDs, statuses).
   sky status
   NAME         LAUNCHED   RESOURCES           STATUS  AUTOSTOP  COMMAND
   mucluster    1 min ago  2x AWS(m4.2xlarge)  UP      0 min     sky launch -y -d -c ...

   # Refresh the status for auto-stopping
   sky status --refresh
   NAME         LAUNCHED   RESOURCES           STATUS  AUTOSTOP  COMMAND
   mucluster    1 min ago  2x AWS(m4.2xlarge)  STOPPED -         sky launch -y -d -c ...


The cluster status in :code:`sky status` shows the cached status of the cluster, which can be out-dated for clusters with auto-stopping scheduled. To view a real status of the cluster with auto-stopping scheduled, use :code:`sky status --refresh`.


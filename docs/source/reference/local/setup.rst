.. _local-setup:

Setting up Local Cluster
===========================

Prerequisites
-------------
To ensure sky nodes can communicate with each other, SkyPilot On-prem requires the system admin to open up all ports from :code:`10001` to :code:`19999`, inclusive, on all nodes. This is how SkyPilot differentiates input/output for multiple worker processes on a single node. In addition, SkyPilot requires port :code:`8265` for Ray Dashboard on all nodes.

For the head node, SkyPilot requires port :code:`6379` for the GCS server on Ray.

For further reference, `here <https://docs.ray.io/en/latest/ray-core/configure.html#ports-configurations>`_ are the required ports directly from the Ray docs.

Installing SkyPilot dependencies
-----------------------------------

SkyPilot On-prem requires :code:`python3`, :code:`ray==2.0.1`, and :code:`sky` to be setup on all local nodes and globally available to all users.

To install Ray and SkyPilot for all users, run the following commands on all local nodes:

.. code-block:: console

   $ pip3 install ray[default]==2.0.1

   $ # SkyPilot requires python >= 3.6.
   $ pip3 install skypilot


Launching SkyPilot services
------------------------------

For SkyPilot to automatically launch the cluster manager, the system administrator needs to fill out a **private** :ref:`cluster YAML <cluster-config>` file. An example of such is provided below:

.. code-block:: yaml

    # Header for cluster specific data.
    cluster:
      # List of IPS/hostnames in the cluster. The first element is the head node.
      ips: [my.local.cluster.hostname, 3.20.226.96, 3.143.112.6]
      name: my-local-cluster

    # How the system admin authenticates into the local cluster.
    auth:
      ssh_user: ubuntu
      ssh_private_key: ~/.ssh/ubuntu.pem


Next, the system admin runs:

.. code-block:: console

   $ sky admin deploy my-cluster-config.yaml

SkyPilot will automatically perform the following 4 tasks:

- Check if the local cluster environment is setup correctly
- Profile the cluster for custom resources, such as GPUs
- Launch SkyPilot's cluster manager
- Generate a public **distributable** cluster YAML, conveniently stored in :code:`~/.sky/local/my-local-cluster.yaml`

Finally, to check if SkyPilot services have been installed correctly, run the following on the head node:

.. code-block::
   
   $ # Check if Ray cluster is launched on all nodes
   $ ray status

   ======== Autoscaler status: 2022-04-27 08:53:44.995448 ========
   Node status
   ---------------------------------------------------------------
   Healthy:
    1 node_788952ec7fb0c6c5cfac0015101952b6593f10913df9bccef44ea346
    1 node_ec653cdb9bc6d4e2d982fa39485f6e4a90be947288ca6c1e5accd843
   Pending:
    (no pending nodes)
   Recent failures:
    (no failures)

   Resources
   ---------------------------------------------------------------
   Usage:
    0.0/64.0 CPU
    0.0/8.0 GPU
    0.0/8.0 V100
    0.00/324.119 GiB memory
    0.00/142.900 GiB object_store_memory

The console should display a list of healthy nodes the size of the local cluster.


Publishing cluster YAML
---------------------------

Under the hood, :code:`sky admin deploy` automatically stores a public **distributable** cluster YAML in :code:`~/.sky/local/my-cluster.yaml`. This cluster YAML follows a similar structure as that of the private cluster YAML, with admin authentication replaced with a placeholder value (for regular users to fill in):

.. code-block:: yaml

    # Do NOT modify ips, OK to modify name
    cluster:
      ips: [my.local.cluster.hostname, 3.20.226.96, 3.143.112.6]
      name: my-local-cluster

    auth:
      ssh_user: PLACEHOLDER
      ssh_private_key: PLACEHOLDER

    # Path to the python binary to be used by SkyPilot. Must be the same on all nodes and executable by all users.
    python: /usr/bin/python3

The distributable cluster YAML can be published on the company's website or sent privately between users. Regular users store this yaml in :code:`~/.sky/local/`, and replace :code:`PLACEHOLDER` with their credentials.

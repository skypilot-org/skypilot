.. _interactive-nodes:
Interactive Nodes
=================

During development, it may be preferable to have direct access to a VM without
specifying a task YAML. Sky provides this functionality by providing interactive nodes
nodes for development sessions. These are by default single node VMs, customizable
with resources of your choice.

Interactive nodes act like other clusters launched with YAML, except they are
easily accessed with command line aliases that automatically log in to the node.

Launching a development machine
-------------------------------
To acquire and log in to an interative node with no accelerators:

.. code-block:: console

   $ sky cpunode -c my-cpu

We can also force a cloud and instance type if required:

.. code-block:: console

   $ sky cpunode -c my-cpu --cloud gcp --instance-type n1-standard-8

All available configuration options can be viewed with:

.. code-block:: console

   $ sky cpunode --help

To get an interactive node with an accelerator, we have
:code:`sky gpunode` and :code:`sky tpunode` as well with similar usage patterns.

To log in to an interactive node:

.. code-block:: bash

    # automatically logs in after provisioning
    sky cpunode -c my-cpu

    # directly logs in
    ssh my-cpu


Because Sky exposes SSH access to interactive nodes, this means they can also be
used with tools such as `Visual Studio Code Remote <https://code.visualstudio.com/docs/remote/remote-overview>`_.


Interactive nodes can be started and stopped like any other cluster:

.. code-block:: bash

    # stop the cluster
    $ sky stop my-cpu

    # restart the cluster
    $ sky start my-cpu

.. note::

    If :code:`sky start` is used to restart a stopped cluster, auto-failover provisioning
    is not used and the cluster will be started on the same cloud and region that it was
    originally provisioned on.


Advanced configuration
----------------------
By default, interactive clusters are a single node. If you require a cluster with multiple nodes
(e.g. for distributed training, etc.), you can launch a cluster using YAML:

.. code-block:: yaml

    # multi_node.yaml

    num_nodes: 16
    resources:
      accelerators: V100:8

.. code-block:: console

    $ sky launch -c my-cluster multi_node.yaml

To log in to the head node:

.. code-block:: console

    $ ssh my-cluster

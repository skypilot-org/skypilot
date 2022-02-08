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
used with tools such as `VSCode Remote <https://code.visualstudio.com/docs/remote/remote-overview>`_.


Interactive nodes can be started and stopped like any other cluster:

.. code-block:: bash

    # stop the cluster
    $ sky stop my-cpu

    # restart the cluster
    $ sky start my-cpu


Advanced configurations
-----------------------
By default, interactive clusters are a single node. For cases where

Also show that if users prefer to have a multi-node cluster here's how they can create
a YAML with no setup or run and can just ssh in.


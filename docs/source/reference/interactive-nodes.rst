.. _interactive-nodes:
Interactive Nodes
=================

Sky provides **interactive nodes**, the user's *personal work servers* in the
clouds.  These are single-node VMs that can be quickly accessed by convenient
CLI commands:

- :code:`sky gpunode`
- :code:`sky cpunode`
- :code:`sky tpunode`

Interactive nodes are normal Sky clusters.  They allow fast access to instances
without requiring a task YAML specification.

Workflow
-------------------------------

Use :code:`sky gpunode` to get a node with GPU(s):

.. code-block:: console

   $ # Create and log in to a cluster with the
   $ # default name, "sky-gpunode-<username>".
   $ sky gpunode

   $ # Or, use -c to set a custom name to manage multiple clusters:
   $ # sky gpunode -c node0

Use :code:`--gpus` to change the type and the number of GPUs:

.. code-block:: console

   $ sky gpunode  # By default, use 1 K80 GPU.
   $ sky gpunode --gpus V100
   $ sky gpunode --gpus V100:8

   $ # To see available GPU names:
   $ # sky show-gpus

Directly set a cloud and an instance type, if required:

.. code-block:: console

   $ sky gpunode --cloud aws --instance-type p2.16xlarge

See all available options and short keys:

.. code-block:: console

   $ sky gpunode --help

Sky also provides :code:`sky cpunode` for CPU-only instances and :code:`sky
tpunode` for TPU instances (only available on Google Cloud Platform).

To log in to an interactive node, either re-type the CLI command or use :code:`ssh`:

.. code-block:: console

    $ # If the cluster with the default name exists, this will directly log in.
    $ sky gpunode

    $ # Equivalently:
    $ ssh sky-gpunode-<username>

    $ # Use -c to refer to different interactive nodes.
    $ # sky gpunode -c node0
    $ # ssh node0

Because Sky exposes SSH access to clusters, this means clusters can be easily added into
tools such as `Visual Studio Code Remote <https://code.visualstudio.com/docs/remote/remote-overview>`_.

Since interactive nodes are just normal Sky clusters, :code:`sky exec` can be used to submit jobs to them.

Interactive nodes can be stopped, restarted, and terminated, like any other cluster:

.. code-block:: console

    $ # Stop at the end of the work day:
    $ sky stop sky-gpunode-<username>

    $ # Restart it the next morning:
    $ sky start sky-gpunode-<username>

    $ # Terminate entirely:
    $ sky down sky-gpunode-<username>

.. note::

    Since :code:`sky start` restarts a stopped cluster, :ref:`auto-failover
    provisioning <auto-failover>` is disabled---the cluster will be restarted on
    the same cloud and region where it was originally provisioned.


Getting multiple nodes
----------------------
By default, interactive clusters are a single node. If you require a cluster
with multiple nodes (e.g., for hyperparameter tuning or distributed training),
use :code:`num_nodes` in a YAML spec:

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

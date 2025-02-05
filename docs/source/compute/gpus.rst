.. _accelerators:

GPUs & Accelerators
============================

SkyPilot supports a wide range of GPUs, TPUs, and other accelerators.

Supported Accelerators
----------------------

.. code-block:: console

   $ sky show-gpus -a

.. literalinclude:: show-gpus-all.txt
   :language: text

Behind the scenes, these details are encoded in the SkyPilot Catalog: https://github.com/skypilot-org/skypilot-catalog.

Accelerators in Kubernetes
--------------------------

Your Kubernetes clusters may contain only certain accelerators.

You can query the accelerators available in your Kubernetes clusters with:

.. code-block:: console

   $ sky show-gpus --cloud k8s


.. code-block:: text

    Kubernetes GPUs
    GPU   REQUESTABLE_QTY_PER_NODE  TOTAL_GPUS  TOTAL_FREE_GPUS
    L4    1, 2, 4                   12          12
    H100  1, 2, 4, 8                16          16

    Kubernetes per node GPU availability
    NODE_NAME                  GPU_NAME  TOTAL_GPUS  FREE_GPUS
    my-cluster-0               L4        4           4
    my-cluster-1               L4        4           4
    my-cluster-2               L4        2           2
    my-cluster-3               L4        2           2
    my-cluster-4               H100      8           8
    my-cluster-5               H100      8           8

Querying Accelerator Details
----------------------------

You can query the details of a supported accelerator config, ``accelerator:count``:

.. code-block:: console

   $ sky show-gpus H100:8

.. literalinclude:: show-gpus-h100-8.txt
   :language: text

Requesting Accelerators
----------------------------

You can use ``accelerator:count`` in various places that accept accelerator specifications.

.. code-block:: console

   $ sky launch --gpus H100:8
   $ sky launch --gpus H100  # If count is omitted, default to 1.
   $ sky exec my-h100-8-cluster --gpus H100:0.5 job.yaml

.. code-block:: yaml

   # In SkyPilot YAML:

   resources:
     accelerators: H100:8

   # Set: ask SkyPilot to auto-choose the cheapest and available option.
   resources:
     accelerators: {H100:8, A100:8}

   # List: ask SkyPilot to try each one in order.
   resources:
     accelerators: [L4:8, L40S:8, A10G:8, A10:8]

See :ref:`auto-failover` for more examples.

Google TPUs
-----------------

See :ref:`tpu`.

.. toctree::
   :maxdepth: 1
   :hidden:

   Using Google TPUs <../../reference/tpu>


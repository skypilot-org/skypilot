.. _sky-serve-high-availability-controller:

=========================================
High Availability Controller
=========================================

High availability mode ensures the controller for Sky Serve remains resilient to failures by running it as a Kubernetes Deployment with automatic restarts and persistent storage. This helps maintain service management capabilities even if the controller pod crashes or the node fails.

To enable high availability for Sky Serve, simply set the ``high_availability`` flag to ``true`` under ``serve.controller`` in your ``~/.sky/config.yaml``, and ensure the controller runs on Kubernetes:

.. code-block:: yaml
    :emphasize-lines: 4-5

    serve:
      controller:
        resources:
          cloud: kubernetes
        high_availability: true

This will deploy the controller as a Kubernetes Deployment with persistent storage, allowing automatic recovery on failures. For prerequisites, setup steps, and recovery behavior, see the detailed page: :ref:`high-availability-controller`.
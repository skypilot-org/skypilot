.. _managed-jobs-high-availability-controller:

=========================================
High Availability Controller
=========================================

High availability mode ensures the controller for Managed Jobs remains resilient to failures by running it as a Kubernetes Deployment with automatic restarts and persistent storage. This helps maintain management capabilities even if the controller pod crashes or the node fails.

To enable high availability for Managed Jobs, simply set the ``high_availability`` flag to ``true`` under ``jobs.controller`` in your ``~/.sky/config.yaml``, and ensure the controller runs on Kubernetes:

.. code-block:: yaml
    :emphasize-lines: 4-5

    jobs:
      controller:
        resources:
          cloud: kubernetes
        high_availability: true

This will deploy the controller as a Kubernetes Deployment with persistent storage, allowing automatic recovery on failures. For prerequisites, setup steps, and recovery behavior, see the detailed page: :ref:`high-availability-controller`.
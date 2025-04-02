.. _sky-api-server-troubleshooting:

Troubleshooting SkyPilot API Server
===================================

.. note::
   If none of the troubleshooting steps below resolve your issue, please do not hesitate to file a GitHub issue at our `issue tracker <https://github.com/skypilot-org/skypilot/issues>`_. Your feedback is valuable in helping us improve SkyPilot!

.. _sky-api-server-troubleshooting-helm:

Helm deployment troubleshooting
-------------------------------

.. _sky-api-server-troubleshooting-pod-pending:

API server pod is pending
^^^^^^^^^^^^^^^^^^^^^^^^^

If the API server pod is pending, you can inspect the pending reason with:

.. code-block:: bash

    kubectl describe pod -n $NAMESPACE -l app=${RELEASE_NAME}-api

If the pending reason is ``FailedScheduling`` and the information indicates there is insufficient cpu/memory, you can either:

- Adding more resources to the Kubernetes cluster, or
- Using a smaller API server resources request:

.. code-block:: bash

    # Update the resources requests while keeping existing values set in the previous commands
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly \
    --namespace $NAMESPACE \
    --reuse-values \
    --set apiService.resources.requests.cpu=2 \
    --set apiService.resources.requests.memory=4Gi

.. note::

    At least 2GB memory request is required for the API server to function properly, otherwise the chart will fail to install.

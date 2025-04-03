.. _sky-api-server-troubleshooting:

Troubleshooting SkyPilot API Server
===================================

If you're unable to run SkyPilot API server, this guide will help you debug common issues.

If this guide does not help resolve your issue, please reach out to us on `Slack <https://slack.skypilot.co>`_ or `GitHub <http://www.github.com/skypilot-org/skypilot>`_.

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
- Using a smaller API server resources request, for example (change the cpu and memory to your desired values):

.. code-block:: bash

    # Update the resources requests while keeping existing values set in the previous commands
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly \
    --namespace $NAMESPACE \
    --reuse-values \
    --set apiService.resources.requests.cpu=4 \
    --set apiService.resources.requests.memory=8Gi

.. note::

    API server requires at least 4 CPU cores and 8 GiB memory, setting lower values may cause degraded performance.

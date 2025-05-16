.. _sky-api-server-troubleshooting:

Troubleshooting SkyPilot API Server
===================================

This guide includes tips for troubleshooting common issues with API server deployment.

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

- Add more resources to the Kubernetes cluster, or
- Use a smaller API server resources request; for example (change the cpu and memory to your desired values):

.. code-block:: bash

    # Update the resources requests while keeping existing values set in the previous commands
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly \
    --namespace $NAMESPACE \
    --reuse-values \
    --set apiService.resources.requests.cpu=4 \
    --set apiService.resources.requests.memory=8Gi

.. note::

    API server requires at least 4 CPU cores and 8 GiB memory. Setting lower values may cause degraded performance.

.. _sky-api-server-troubleshooting-ingress-nginx-conflicts:

Ingress-nginx controller conflicts
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you encounter similar errors as below when deploying the API server:

.. code-block:: console

    Error: Unable to continue with install: IngressClass "nginx" in namespace "" exists and cannot be imported into the current release: invalid ownership metadata; annotation validation error: key "meta.helm.sh/release-name" must equal "test": current value is "skypilot"

It is likely you have an existing ingress-nginx controller deployed in the Kubernetes cluster. Or have an existing SkyPilot API server deployed which by default installs an ingress-nginx controller. If this is the case, you can follow the instructions in :ref:`Reusing ingress controller for API server <sky-api-server-helm-multiple-deploy>` to reuse the existing ingress-nginx controller to solve this issue.


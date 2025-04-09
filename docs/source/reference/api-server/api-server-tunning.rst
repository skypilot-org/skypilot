.. _sky-api-server-performance-best-practices:

SkyPilot API Server Performance Best Practices
==============================================

This page describes performance best practices for centralized SkyPilot API server in team deployment.

This guide assumes you have already deployed the API server following the :ref:`sky-api-server-deploy` guide using helm. Similar tuning strategies also apply to VM deployment, refer to :ref:`sky-api-server-cloud-deploy` for details.

Customizing API server resources
--------------------------------

API server is a single-node deployment. By default, the API server is deployed with the minimum recommended resources for team usage. You can customize the resources of the API server to fit your team's workload by specifying `apiService.resources.requests.cpu` and `apiService.resources.requests.memory` in the helm values.

.. code-block:: bash

    # NAMESPACE and RELEASE_NAME should be the same as the deployment step
    helm upgrade -n ${NAMESPACE} ${RELEASE_NAME} skypilot/skypilot-nightly \
        --reuse-values \
        --set apiService.resources.requests.cpu=8 \
        --set apiService.resources.requests.memory=16Gi

.. note::

    The API server will be automatically restarted to apply the new resources, which will introduce a downtime. Refer to :ref:`sky-api-server-deploy` for more details.

If you specify a resources that is lower than the minimum recommended resources for team usage, an error will be raised on ``helm upgrade``. You can specify ``--set apiService.skipResourcesCheck=true`` to skip the check if performance and stability is not an issue for you scenario.


.. _external-logging-storage:

External Logging Storage
========================

SkyPilot supports sending logs to external logging storage for persistence and unified observability.

To enable external logging storage, set the following in your SkyPilot config:

.. code-block:: yaml

    logs:
      store: gcp

.. note::

    Only clusters provisioned after enabling external logging storage will have logs sent to the external logging storage.

Each logging storage might have its own configuration options under ``logs.<store>`` structure. The supported logging storages and their configuration options are listed below:

- ``gcp``: :ref:`GCP Cloud logging <external-logging-storage-gcp>`

.. _external-logging-storage-gcp:

Google cloud logging
~~~~~~~~~~~~~~~~~~~~

Configuration options:

- ``project_id``: The project ID of the GCP project to send logs to.

Example:

.. code-block:: yaml

  logs:
    store: gcp
    gcp:
      project_id: my-project-id

Available filters:

- ``jsonPayload.log``: Filter on the log content directly.
- ``jsonPayload.log_path``: Filter on the log path, which has 
- ``labels.skypilot_cluster_name``: The name of the SkyPilot cluster.
- ``labels.skypilot_cluster_id``: The unique ID of the SkyPilot cluster, a cluster with the same name terminated and launched for multiple times will have different IDs.

Example querys:

.. code-block:: bash

    # Get logs of a specific cluster by name
    gcloud logging read "labels.skypilot_cluster_name=my-cluster-name" --format=json | jq -r ".[].jsonPayload.log"

    # Get logs of a specific job by name
    gcloud logging read "jsonPayload.log_path:my-job-name" --format=json | jq -r ".[].jsonPayload.log"

You can open the `Cloud logging explorer <https://console.cloud.google.com/logs/explorer>`_ and filter the logs by the above filters.

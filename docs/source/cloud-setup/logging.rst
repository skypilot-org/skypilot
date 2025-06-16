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
- ``credentials_file``: The path to the GCP credentials file, refer to :ref:`Authenticating to GCP cloud logging<external-logging-storage-gcp-authentication>` for more details.
- ``additional_labels``: Additional labels to add to the logs.

Example:

.. code-block:: yaml

  logs:
    store: gcp
    gcp:
      project_id: my-project-id
      credentials_file: /path/to/credentials.json
      additional_labels:
        my_label: my_value

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

.. _external-logging-storage-gcp-authentication:

Authenticating to GCP cloud logging
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To authenticate to GCP cloud logging, SkyPilot will apply credentials in the following precedence:

- The `service account key <https://cloud.google.com/iam/docs/keys-create-delete>`_ specified by ``logs.gcp.credentials_file``;
- The default GCP credential of the API server, if the credential is a service account key;
- `Instance metadata <https://cloud.google.com/compute/docs/metadata/overview>`_, which is only available on GCE and GCP.

.. note::

  In :ref:`client-server architecture<sky-api-server>`, the ``logs.gcp.credentials_file`` refers to the credential file on the remote API server instead of the local machine.

The credentials used must have the following permissions to send logs to GCP cloud logging:

- ``logging.logEntries.create``
- ``logging.logEntries.route``


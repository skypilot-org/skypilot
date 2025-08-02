.. _external-logging-storage:

External Logging Storage
========================

SkyPilot supports sending logs to external logging storage for persistence and unified observability.
The supported logging services are:

- ``aws``: :ref:`AWS CloudWatch <external-logging-storage-aws>`
- ``gcp``: :ref:`GCP Cloud Logging <external-logging-storage-gcp>`

To enable external logging storage, set the following in your SkyPilot config:

.. code-block:: yaml

    logs:
      store: aws  # Or 'gcp', etc.
      aws:
        ...  # Service-specific options; see below.

Each logging service might have its own configuration options under the ``logs.<store>`` dict.

.. note::

    Only clusters provisioned after enabling external logging storage will have logs sent to the external logging storage.


.. _external-logging-storage-aws:

AWS CloudWatch logging
~~~~~~~~~~~~~~~~~~~~~~

Configuration options:

- ``region``: The AWS region for CloudWatch logs (e.g., ``us-west-2``). If not specified, SkyPilot will try to use the region from the environment variables or instance metadata.
- ``credentials_file``: The path to the AWS credentials file, refer to :ref:`Authenticating to AWS CloudWatch<external-logging-storage-aws-authentication>` for more details.
- ``log_group_name``: The name of the CloudWatch log group (default: ``skypilot-logs``).
- ``log_stream_prefix``: Prefix for log stream names (default: ``skypilot-``).
- ``auto_create_group``: Whether to automatically create the log group if it doesn't exist (default: ``true``).
- ``additional_tags``: Additional tags to add to the logs.

Example:

.. code-block:: yaml

  logs:
    store: aws
    aws:
      region: us-west-2
      credentials_file: /path/to/credentials
      log_group_name: my-skypilot-logs
      log_stream_prefix: my-cluster-
      auto_create_group: true
      additional_tags:
        environment: production

Available filters in CloudWatch Logs Insights:

- ``@message``: Filter on the log content directly.
- ``@logStream``: Filter on the log stream name, which includes the cluster ID.
- ``skypilot.cluster_name``: The name of the SkyPilot cluster.
- ``skypilot.cluster_id``: The unique ID of the SkyPilot cluster.

Example queries:

.. code-block:: bash

    # Get logs of a specific cluster by name using AWS CLI
    aws logs filter-log-events --log-group-name my-skypilot-logs --filter-pattern 'skypilot.cluster_name = "my-cluster-name"' | | jq -r ".events[].message | fromjson | .log"

    # Get logs of a specific job by name using AWS CLI
    aws logs filter-log-events --log-group-name my-skypilot-logs --filter-pattern '%my-job-name%' | | jq -r ".events[].message | fromjson | .log"

    # Using CloudWatch Logs Insights
    fields @timestamp, @message
    | filter skypilot.cluster_name = 'my-cluster-name'
    | sort @timestamp desc
    | limit 100

You can also use the `CloudWatch Logs console <https://console.aws.amazon.com/cloudwatch/home#logsV2:logs-insights>`_ to query logs using CloudWatch Logs Insights.

.. _external-logging-storage-aws-authentication:

Authenticating to AWS CloudWatch
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To authenticate to AWS CloudWatch, SkyPilot will apply credentials in the following precedence:

1. **IAM Roles**: When running on EC2 instances with IAM roles (preferred method)
2. **Environment Variables**: ``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY``
3. **Shared Credentials File**: The credentials file specified by ``logs.aws.credentials_file`` or the default location (``~/.aws/credentials``)

.. note::

  In :ref:`client-server architecture<sky-api-server>`, the ``logs.aws.credentials_file`` refers to the credential file on the remote API server instead of the local machine.

The credentials used must have the following permissions to send logs to AWS CloudWatch:

- ``logs:CreateLogGroup``
- ``logs:CreateLogStream``
- ``logs:PutLogEvents``
- ``logs:DescribeLogStreams``

Example IAM policy:

.. code-block:: json

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
            "logs:DescribeLogStreams"
          ],
          "Resource": [
            "arn:aws:logs:*:*:log-group:skypilot-logs:*"
          ]
        }
      ]
    }

.. _external-logging-storage-gcp:

Google Cloud Logging
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
- ``jsonPayload.log_path``: Filter on the log path, which has job name in the path.
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

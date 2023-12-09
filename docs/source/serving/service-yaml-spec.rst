.. _service-yaml-spec:

Service YAML Specification
==========================

SkyServe provides an intuitive YAML interface to specify a service. It is highly similar to the :ref:`SkyPilot task YAML <yaml-spec>`: with an additional service section in your original task YAML, you could change it to a service YAML.

Available fields:


.. code-block:: yaml

    # Additional section to turn your skypilot task.yaml to a service
    service:

      # Readiness probe (required). This describe how SkyServe determine your
      # service is ready for accepting traffic. If the readiness probe get a 200,
      # SkyServe will start routing traffic to your service.
      readiness_probe:
        # Path to probe (required).
        path: /v1/models
        # Post data (optional). If this is specified, the readiness probe will use
        # POST instead of GET, and the post data will be sent as the request body.
        post_data: {'model_name': 'model'}
        # Initial delay in seconds (optional). Defaults to 1200 seconds (20 minutes).
        # Any readiness probe failures during this period will be ignored. This is
        # highly related to your service, so it is recommended to set this value
        # based on your service's startup time.
        initial_delay_seconds: 1200

      # We have a simplified version of readiness probe that only contains the
      # readiness probe path. If you want to use GET method for readiness probe
      # and the default initial delay, you can use the following syntax:
      readiness_probe: /v1/models

      # One of the two following fields (replica_policy or replicas) is required.

      # Replica autoscaling policy. This describes how SkyServe autoscales
      # your service based on the QPS (queries per second) of your service.
      replica_policy:
        # Minimum number of replicas (required).
        min_replicas: 1
        # Maximum number of replicas (optional). If not specified, SkyServe will
        # use fixed number of replicas same as min_replicas and ignore any QPS
        # threshold specified below.
        max_replicas: 3
        # Following thresholds describe when to scale up or down.
        # QPS threshold for scaling up (optional). If the QPS of your service
        # exceeds this threshold, SkyServe will scale up your service by one
        # replica. If not specified, SkyServe will **NOT** scale up your service.
        qps_upper_threshold: 10
        # QPS threshold for scaling down (optional). If the QPS of your service
        # is below this threshold, SkyServe will scale down your service by one
        # replica. If not specified, SkyServe will **NOT** scale down your service.
        qps_lower_threshold: 2

      # Also, for convenience, we have a simplified version of replica policy that
      # use fixed number of replicas. Just use the following syntax:
      replicas: 2

      # Controller resources (optional). This describe the resources to use for
      # the controller. Default to a 4+ vCPU instance with 100GB disk.
      controller_resources:
        cloud: aws
        region: us-east-1
        instance_type: p3.2xlarge
        disk_size: 256

    resources:
      # Port to run your service (required). This port will be automatically exposed
      # by SkyServe. You can access your service at http://<endpoint-ip>:<port>.
      ports: 8080
      # Other resources config...

    # Then comes your SkyPilot task YAML...


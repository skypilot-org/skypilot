.. _service-yaml-spec:

Service YAML
==========================

SkyServe provides an intuitive YAML interface to specify a service. It is highly similar to the :ref:`SkyPilot task YAML <yaml-spec>`: with an additional service section in your original task YAML, you could change it to a service YAML.

Available fields:


.. code-block:: yaml

    # The `service` section turns a skypilot task yaml into a service yaml.
    service:

      # Readiness probe (required). Used by SkyServe to check if your service
      # replicas are ready for accepting traffic. If the readiness probe returns
      # a 200, SkyServe will start routing traffic to that replica.
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

      # Simplified version of readiness probe that only contains the readiness
      # probe path. If you want to use GET method for readiness probe and the
      # default initial delay, you can use the following syntax:
      readiness_probe: /v1/models

      # One of the two following fields (replica_policy or replicas) is required.

      # Replica autoscaling policy. This describes how SkyServe autoscales
      # your service based on the QPS (queries per second) of your service.
      replica_policy:
        # Minimum number of replicas (required).
        min_replicas: 1
        # Maximum number of replicas (optional). If not specified, SkyServe will
        # use a fixed number of replicas (the same as min_replicas) and ignore
        # any QPS threshold specified below.
        max_replicas: 3
        # Following specs describe the autoscaling policy.
        # Target query per second per replica (optional). SkyServe will scale your
        # service so that, ultimately, each replica manages approximately
        # target_qps_per_replica queries per second. **Autoscaling will only be
        # enabled if this value is specified.**
        target_qps_per_replica: 5
        # Upscale and downscale delay in seconds (optional). Defaults to 300 seconds
        # (5 minutes) and 1200 seconds (20 minutes) respectively. To avoid aggressive
        # autoscaling, SkyServe will only upscale or downscale your service if the
        # QPS of your service is higher or lower than the target QPS for a period
        # of time. This period of time is controlled by upscale_delay_seconds and
        # downscale_delay_seconds. The default values should work in most cases.
        # If you want to scale your service more aggressively, you can set
        # these values to a smaller number.
        upscale_delay_seconds: 300
        downscale_delay_seconds: 1200
      # Simplified version of replica policy that uses a fixed number of
      # replicas:
      replicas: 2

    ##### Fields below describe each replica #####

    # Besides the `service` section, the rest is a regular SkyPilot task YAML.

    resources:
      # Port to run your service on each replica (required). This port will be
      # automatically exposed to the public internet by SkyServe.
      ports: 8080
      # Other resources config...

    # Other fields of your SkyPilot task YAML...


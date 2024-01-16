.. _serve-autoscaling:

Autoscaling
===========

SkyServe provides out-of-the-box autoscaling for your services. In a regular SkyServe Service, number of replica to launch is specified in the service section:

.. code-block:: yaml
    :emphasize-lines: 3

    service:
      readiness_probe: /
      replicas: 2

    # ...

In this case, SkyServe will launch 2 replicas of your service. However, this deployment is fixed and cannot response to dynamic traffics. SkyServe provides autoscaling feature to help you scale your service up and down based on the traffic.

Minimal Example
---------------

Following is a minimal example to enable autoscaling for your service:

.. code-block:: yaml
    :emphasize-lines: 3-6

    service:
      readiness_probe: /
      replica_policy:
        min_replicas: 2
        max_replicas: 10
        target_qps_per_replica: 3

    # ...

In this example, SkyServe will launch 2 replicas of your service and scale up to 10 replicas if the traffic is high. The autoscaling is based on the QPS (Queries Per Second) of your service. SkyServe will scale your service so that, ultimately, each replica manages approximately :code:`target_qps_per_replica` queries per second. If the QPS is higher than 3 per replica, SkyServe will launch more replicas and scale up to 10 replicas. If the QPS is lower than 3 per replica, SkyServe will scale down the replicas to 2. Specifically, the current target number of replicas is calculated as:

.. code-block:: python

    current_target_replicas = ceil(current_qps / target_qps_per_replica)
    final_target_replicas = min(max_replicas, max(min_replicas, current_target_replicas))

.. tip::

    :code:`replica` is a shortcut for :code:`replica_policy.min_replicas`. These two fields cannot be specified at the same time.

Scaling Delay
-------------

SkyServe will not scale up or down immediately. Instead, SkyServe will wait for a period of time before scaling up or down. This is to avoid scaling up and down too aggressive. SkyServe will only upscale or downscale your service if the QPS of your service is higher or lower than the target QPS for a period of time. The default scaling delay is 300s for upscale and 1200s for downscale. You can change the scaling delay by specifying the :code:`upscale_delay_seconds` and :code:`downscale_delay_seconds` field in the autoscaling section:

.. code-block:: yaml
    :emphasize-lines: 7-8

    service:
      readiness_probe: /
      replica_policy:
        min_replicas: 2
        max_replicas: 10
        target_qps_per_replica: 3
        upscale_delay_seconds: 600
        downscale_delay_seconds: 1800

    # ...

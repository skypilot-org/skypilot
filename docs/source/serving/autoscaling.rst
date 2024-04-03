.. _serve-autoscaling:

Autoscaling
===========

SkyServe provides out-of-the-box autoscaling for your services.

Fixed Replicas
--------------

In a service YAML, the number of replicas to launch is specified in the ``service`` section's ``replicas`` field:

.. code-block:: yaml
    :emphasize-lines: 3

    service:
      readiness_probe: /
      replicas: 2

    # ...

In this case, SkyServe will launch 2 replicas of your service. However, this deployment is fixed and cannot adjust to dynamic traffic.
SkyServe provides autoscaling to help you scale your service up and down based on traffic, as shown below.

Enabling Autoscaling
--------------------

Here is a minimal example to enable autoscaling for your service:

.. code-block:: yaml
    :emphasize-lines: 3-6

    service:
      readiness_probe: /
      replica_policy:
        min_replicas: 2
        max_replicas: 10
        target_qps_per_replica: 2.5

    # ...

In this example, SkyServe will:

- Initially, launch 2 replicas of your service (``min_replicas``)
- Scale up gradually if the traffic is high, up to 10 replicas (``max_replicas``)
- Scale down gradually if the traffic is low, with a minimum of 2 replicas (``min_replicas``)

The replica count will always be in the range
:code:`[min_replicas, max_replicas]`.

Autoscaling is performed based on the QPS (Queries Per Second) of your service.
SkyServe will scale your service so that, ultimately, each replica receives
approximately :code:`target_qps_per_replica` queries per second.
This value can be a float; for example:

- If the QPS is higher than 2.5 per replica, SkyServe will launch more replicas (but no more than 10 replicas)
- If the QPS is lower than 2.5 per replica, SkyServe will scale down the replicas (but no less than 2 replicas)

Specifically, the current target number of replicas is calculated as:

.. code-block:: python

    current_target_replicas = ceil(current_qps / target_qps_per_replica)
    final_target_replicas = min(max_replicas, max(min_replicas, current_target_replicas))

.. tip::

    :code:`replicas` is a shortcut for :code:`replica_policy.min_replicas`. These two fields cannot be specified at the same time.

.. tip::

    :code:`target_qps_per_replica` can be any positive floating point number. If processing one request takes two seconds in one replica, we can use :code:`target_qps_per_replica=0.5`.

Scaling Delay
-------------

SkyServe will not scale up or down immediately. Instead, SkyServe will only
upscale or downscale your service if the QPS of your service is higher or lower
than the target QPS for a period of time.  This is to avoid scaling up and down
too aggressively.

The default scaling delay is 300s for upscale and 1200s for downscale. You can
change the scaling delay by specifying the :code:`upscale_delay_seconds` and
:code:`downscale_delay_seconds` fields:

.. code-block:: yaml
    :emphasize-lines: 7-8

    service:
      readiness_probe: /
      replica_policy:
        min_replicas: 2
        max_replicas: 10
        target_qps_per_replica: 3
        upscale_delay_seconds: 300
        downscale_delay_seconds: 1200

    # ...

If you want more aggressive scaling, set those values to a lower number and vice versa.

Scale-to-Zero
-------------

SkyServe supports scale-to-zero.

If your service might experience long periods of time with no traffic, consider using :code:`min_replicas: 0`:

.. code-block:: yaml
    :emphasize-lines: 4

    service:
      readiness_probe: /
      replica_policy:
        min_replicas: 0
        max_replicas: 3
        target_qps_per_replica: 6.3

    # ...

The service will scale down all replicas when there is no traffic to the system and will save costs on idle replicas. When upscaling from zero, the upscale delay will be ignored in order to bring up the service faster.

.. tip::

    If the scale-to-zero is set, the clients that access the endpoint should make sure to have a retry mechanism to be able to wait until the replicas are provisioned and ready, i.e., starting a new replica when there is zero replica available.

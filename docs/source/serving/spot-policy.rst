.. _spot_policy:

Using Spot Instances for Serving
================================

SkyServe supports serving models on a mixture of spot and on-demand replicas with two options: :code:`base_ondemand_fallback_replicas` and :code:`dynamic_ondemand_fallback`. Currently, SkyServe relies on the user side to retry in the event of spot instance preemptions.

Base on-demand fallback
-----------------------

:code:`base_ondemand_fallback_replicas` sets the number of on-demand replicas to keep running at all times. This is useful for ensuring service availability and making sure that there is always some capacity available, even if spot replicas are not available. :code:`use_spot` should be set to :code:`true` to enable spot replicas.

.. code-block:: yaml

    service:
      readiness_probe: /health
      replica_policy:
        min_replicas: 2
        max_replicas: 3
        target_qps_per_replica: 1
        # Ensures that one of the replicas is run on on-demand instances
        base_ondemand_fallback_replicas: 1

    resources:
      ports: 8081
      cpus: 2+
      use_spot: true

    workdir: examples/serve/http_server

    run: python3 server.py


.. tip::

    Kubernetes instances are considered on-demand instances. You can use the :code:`base_ondemand_fallback_replicas` option to have some replicas run on Kubernetes, while others run on cloud spot instances.

Dynamic on-demand fallback
--------------------------

SkyServe supports dynamically fallback to on-demand replicas when spot replicas are not available.
This is enabled by setting :code:`dynamic_ondemand_fallback` to be :code:`true`.
This is useful for ensuring the required capacity of replicas in the case of spot instance interruptions.
When spot replicas are available, SkyServe will automatically switch back to using spot replicas to maximize cost savings.

.. code-block:: yaml

    service:
      readiness_probe: /health
      replica_policy:
        min_replicas: 2
        max_replicas: 3
        target_qps_per_replica: 1
        # Allows replicas to be run on on-demand instances if spot instances are not available
        dynamic_ondemand_fallback: true

    resources:
      ports: 8081
      cpus: 2+
      use_spot: true

    workdir: examples/serve/http_server

    run: python3 server.py


.. tip::

    SkyServe supports specifying both :code:`base_ondemand_fallback_replicas` and :code:`dynamic_ondemand_fallback`. Specifying both will set a base number of on-demand replicas and dynamically fallback to on-demand replicas when spot replicas are not available.

Example
-------

The following example demonstrates how to use spot replicas with SkyServe with dynamic fallback. The example is a simple HTTP server that listens on port 8081 with :code:`dynamic_ondemand_fallback: true`. To run:

.. code-block:: console

    $ sky serve up examples/serve/spot_policy/dynamic_on_demand_fallback.yaml -n http-server

When the service is up, we can check the status of the service and the replicas using the following command. Initially, we will see:

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS      REPLICAS  ENDPOINT
    http-server  1        1m 17s  NO_REPLICA  0/4       54.227.229.217:30001

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT  LAUNCHED    INFRA                RESOURCES                                      STATUS         
    http-server   1   1        -         1 min ago   GCP (us-east1)       1x[spot](cpus=2, mem=8, n2-standard-2, ...)   PROVISIONING  
    http-server   2   1        -         1 min ago   GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   PROVISIONING  
    http-server   3   1        -         1 mins ago  GCP (us-east1)       1x(cpus=2, mem=8, n2-standard-2, ...)         PROVISIONING  
    http-server   4   1        -         1 min ago   GCP (us-central1)    1x(cpus=2, mem=8, n2-standard-2, ...)         PROVISIONING  

When the required number of spot replicas are not available, SkyServe will provision on-demand replicas to meet the target number of replicas. For example, when the target number is 2 and no spot replicas are ready, SkyServe will provision 2 on-demand replicas to meet the target number of replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1        1m 17s  READY   2/4       54.227.229.217:30001

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED    INFRA                RESOURCES                                      STATUS         
    http-server   1   1        http://34.23.22.160:8081   3 min ago   GCP (us-east1)       1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY          
    http-server   2   1        http://34.68.226.193:8081  3 min ago   GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY          
    http-server   3   1        -                          3 mins ago  GCP (us-east1)       1x(cpus=2, mem=8, n2-standard-2, ...)         SHUTTING_DOWN  
    http-server   4   1        -                          3 min ago   GCP (us-central1)    1x(cpus=2, mem=8, n2-standard-2, ...)         SHUTTING_DOWN  

When the spot replicas are ready, SkyServe will automatically scale down on-demand replicas to maximize cost savings.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1        3m 59s  READY   2/2       54.227.229.217:30001

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED    INFRA                RESOURCES                                      STATUS         
    http-server   1   1        http://34.23.22.160:8081   4 mins ago  GCP (us-east1)       1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY          
    http-server   2   1        http://34.68.226.193:8081  4 mins ago  GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY          

In the event of spot instance interruptions (e.g. replica 1), SkyServe will automatically fallback to on-demand replicas (e.g. launch one on-demand replica) to meet the required capacity of replicas. SkyServe will continue trying to provision one spot replica in the event where spot availability is back. Note that SkyServe will try different regions and clouds to maximize the chance of successfully provisioning spot instances.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1        7m 2s   READY   1/3       54.227.229.217:30001

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED     INFRA                RESOURCES                                      STATUS         
    http-server   2   1        http://34.68.226.193:8081  7 mins ago   GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY         
    http-server   5   1        -                          13 secs ago  GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   PROVISIONING  
    http-server   6   1        -                          13 secs ago  GCP (us-central1)    1x(cpus=2, mem=8, n2-standard-2, ...)         PROVISIONING  

Eventually, when the spot availability is back, SkyServe will automatically scale down on-demand replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1        10m 5s  READY   2/3       54.227.229.217:30001

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                   LAUNCHED     INFRA                RESOURCES                                      STATUS         
    http-server   2   1        http://34.68.226.193:8081  10 mins ago  GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY          
    http-server   5   1        http://34.121.49.94:8081   1 min ago    GCP (us-central1)    1x[spot](cpus=2, mem=8, n2-standard-2, ...)   READY          

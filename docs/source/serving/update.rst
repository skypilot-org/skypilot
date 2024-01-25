.. _update:

Update
===========

SkyServe supports rolling update for your services. Use ``sky serve update`` to update an existing service:

.. code-block:: console

    $ sky serve update service-name new_service.yaml

SkyServe will launch new replicas described by ``new_service.yaml``. When the number of new replicas reaches the minimum number of replicas (``min_replicas``) required for the service, SkyServe will scale down old replicas to save cost. SkyServe allows users to update ``replica_policy`` parameters, such as ``target_qps_per_replica``. SkyServe also allows users to update ``resources`` parameters, such as ``cpu`` and ``memory``, so that new replicas can be launched on VMs of different types.  

SkyServe does not mix traffic from old and new replicas and will not send traffic to new replicas until ``min_replicas`` new replicas are ready to serve user requests. Before that, SkyServe will only send traffic to the old replicas. 

.. tip::

  :code:`sky serve status` will highlight the latest service version and each replica's version. 

Example
===========

We first launch an HTTP service: 

.. code-block:: console

    $ sky serve up examples/serve/http_server/task.yaml -n http-server

We can use :code:`sky serve status http-server` to check the status of the service:

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT              
    http-server  1        1m 41s  READY   2/2       44.206.240.249:30002  

    Service Replicas
    SERVICE_NAME  ID  VERSION  IP              LAUNCHED    RESOURCES       STATUS  REGION     
    http-server   1   1        54.173.203.169  2 mins ago  1x AWS(vCPU=2)  READY   us-east-1  
    http-server   2   1        52.87.241.103   2 mins ago  1x AWS(vCPU=2)  READY   us-east-1 

Service ``http-server`` has an initial version of 1. Suppose we want to update the service to use 4 CPUs instead of 2, we can update the task yaml ``examples/serve/http_server/task.yaml``, by changing the ``cpu`` parameter from 2 to 4. We can then use :code:`sky serve update` to update the service:

.. code-block:: console

    $ sky serve update http-server examples/serve/http_server/task.yaml

SkyServe will first launch two new replicas with 4 CPUs. When the number of new replicas reaches the ``min_replicas`` (i.e., 2) required for the service, SkyServe will scale down old replicas to save cost. The service's version is updated from 1 to 2. The replicas with ID 3 and 4 are the new replicas with 4 CPUs. The replicas with ID 1 and 2 are the old replicas with 2 CPUs. When the new replicas are still provisioning, SkyServe will only send traffic to the old replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT              
    http-server  2        6m 15s  READY   2/4       44.206.240.249:30002  

    Service Replicas
    SERVICE_NAME  ID  VERSION  IP              LAUNCHED     RESOURCES       STATUS        REGION     
    http-server   1   1        54.173.203.169  6 mins ago   1x AWS(vCPU=2)  READY         us-east-1  
    http-server   2   1        52.87.241.103   6 mins ago   1x AWS(vCPU=2)  READY         us-east-1  
    http-server   3   2        -               21 secs ago  1x AWS(vCPU=4)  PROVISIONING  us-east-1  
    http-server   4   2        -               21 secs ago  1x AWS(vCPU=4)  PROVISIONING  us-east-1

The old replicas will be scaled down when the new replicas are ready. At this point, SkyServe will start sending traffic to the new replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT              
    http-server  2        10m 4s  READY   2/4       44.206.240.249:30002  

    Service Replicas
    SERVICE_NAME  ID  VERSION  IP              LAUNCHED     RESOURCES       STATUS         REGION     
    http-server   1   1        54.173.203.169  10 mins ago  1x AWS(vCPU=2)  SHUTTING_DOWN  us-east-1  
    http-server   2   1        52.87.241.103   10 mins ago  1x AWS(vCPU=2)  SHUTTING_DOWN  us-east-1  
    http-server   3   2        3.93.241.163    1 min ago    1x AWS(vCPU=4)  READY          us-east-1  
    http-server   4   2        18.206.226.82   1 min ago    1x AWS(vCPU=4)  READY          us-east-1

Eventually, we will only have new replicas ready to serve user requests. 

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT              
    http-server  2        11m 42s  READY   2/2       44.206.240.249:30002  

    Service Replicas
    SERVICE_NAME  ID  VERSION  IP             LAUNCHED    RESOURCES       STATUS  REGION     
    http-server   3   2        3.93.241.163   3 mins ago  1x AWS(vCPU=4)  READY   us-east-1  
    http-server   4   2        18.206.226.82  3 mins ago  1x AWS(vCPU=4)  READY   us-east-1

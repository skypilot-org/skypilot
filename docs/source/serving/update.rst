.. _serve-update:

Updating a Service
==================

SkyServe supports *updating* a deployed service, which can be used to change:

* Replica code (e.g., ``run``/``setup``; useful for debugging)
* Replica resource spec in ``resources`` (e.g., accelerator or instance type)
* Service spec in ``service`` (e.g., number of replicas or autoscaling spec)

During an update, the service will remain accessible with no downtime and its
endpoint will remain the same.

To update an existing service, use ``sky serve update``:

.. code-block:: console

    $ sky serve update service-name new_service.yaml

SkyServe will launch new replicas described by ``new_service.yaml`` with the following behavior:

* An update is initiated, and traffic will continue to be redirected to existing (old) replicas.
* New replicas (with new settings) are brought up in the background.
* Once ``min_replicas`` new replicas are ready, new traffic will start to be redirected to the new
  replicas, while old replicas will stop receiving traffic and will be scaled down.


For example, suppose we have a running service hosting an AI model with the following resource configuration:

.. code-block:: yaml

    resources:
      memory: 32+
      accelerators: T4

It is possible to update it to use a new resource configuration for all replicas, such as:

.. code-block:: yaml

    resources:
      memory: 128+
      accelerators: A100

To support updates, a service and its replicas are versioned (starting from 1).
During an update, traffic is entirely serviced by either old-versioned or
new-versioned replicas.  :code:`sky serve status` shows the latest service
version and each replica's version.

Example
~~~~~~~

We first launch a `simple HTTP service <https://github.com/skypilot-org/skypilot/blob/master/examples/serve/http_server/task.yaml>`_:

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

Service ``http-server`` has an initial version of 1.

Suppose we want to update the service to use 4 vCPUs instead of 2. We can update
the task yaml ``examples/serve/http_server/task.yaml``, by changing the ``cpu``
field:

.. code-block:: yaml
    :emphasize-lines: 10

    # examples/serve/http_server/task.yaml
    service:
      readiness_probe:
        path: /health
        initial_delay_seconds: 20
      replicas: 2

    resources:
      ports: 8081
      cpus: 4+

    workdir: examples/serve/http_server

    run: python3 server.py

We can then use :code:`sky serve update` to update the service:

.. code-block:: console

    $ sky serve update http-server examples/serve/http_server/task.yaml

SkyServe will trigger launching two new replicas with 4 vCPUs. Before
``min_replicas`` (set to ``service.replicas`` when unspecified; i.e., 2) new
replicas are ready, SkyServe will only send traffic to the old replicas.  When
the number of new replicas reaches ``min_replicas``, SkyServe will scale down
old replicas to save cost. The service's version is updated from 1 to 2.
Replicas 3 and 4 are the new replicas with 4 vCPUs.  Replicas 1 and 2 are the
old replicas with 2 vCPUs.

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

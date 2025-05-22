.. _serve-update:

Updating a Service
==================

SkyServe supports *updating* a deployed service, which can be used to change:

* Replica code (e.g., ``run``/``setup``; useful for debugging)
* Replica resource spec in ``resources`` (e.g., accelerator or instance type)
* Service spec in ``service`` (e.g., number of replicas or autoscaling spec)

During an update, the service will remain accessible with no downtime and its
endpoint will remain the same. By default, :ref:`rolling update <rolling-update>`
is applied, while you can also specify a :ref:`blue-green update <blue-green-update>`.

.. _rolling-update:

Rolling update
---------------

To update an existing service, use ``sky serve update``:

.. code-block:: console

    $ sky serve update service-name new_service.yaml

SkyServe will launch new replicas described by ``new_service.yaml`` with the following behavior:

* An update is initiated, and traffic will continue to be redirected to existing (old) replicas.
* New replicas (with new settings) are brought up in the background.
* Whenever the total number of old and new replicas exceeds the expected number of replicas (based on autoscaler's decision), extra old replicas will be scaled down.
* Traffic will be redirected to both old and new replicas until all new replicas are ready.

.. hint::

  When only the ``service`` field is updated and no ``workdir`` or ``file_mounts`` is specified in the service task, SkyServe will reuse the old replicas
  by applying the new service spec and bumping its version (See :code:`sky serve status` for the versions). This will significantly reduce the time to
  update the service and avoid potential quota issues.

Example
~~~~~~~~

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
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED    INFRA                RESOURCES                                 STATUS  
    http-server   1   1        http://54.173.203.169:8081  2 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY   
    http-server   2   1        http://52.87.241.103:8081   2 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY   

Service ``http-server`` has an initial version of 1.

Suppose we want to update the service to have 3 replicas instead of 2. We can update
the task yaml ``examples/serve/http_server/task.yaml``, by changing the ``replicas``
field:

.. code-block:: yaml
    :emphasize-lines: 6

    # examples/serve/http_server/task.yaml
    service:
      readiness_probe:
        path: /health
        initial_delay_seconds: 20
      replicas: 3

    resources:
      ports: 8081
      cpus: 2+

    workdir: examples/serve/http_server

    run: python3 server.py

We can then use :code:`sky serve update` to update the service:

.. code-block:: console

    $ sky serve update http-server examples/serve/http_server/task.yaml

SkyServe will trigger launching three new replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  2        6m 15s  READY   2/5       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                 STATUS       
    http-server   1   1        http://54.173.203.169:8081  6 mins ago   AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY         
    http-server   2   1        http://52.87.241.103:8081   6 mins ago   AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY         
    http-server   3   2        -                           21 secs ago  AWS (us-east-1b)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING  
    http-server   4   2        -                           21 secs ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING  
    http-server   5   2        -                           21 secs ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING  


Whenever a new replica is ready, the traffic will be redirected to both old and new replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1,2        10m 4s  READY   3/5       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                 STATUS        
    http-server   1   1        http://54.173.203.169:8081  10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY          
    http-server   2   1        http://52.87.241.103:8081   10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY          
    http-server   3   2        http://3.93.241.163:8081    1 min ago    AWS (us-east-1b)     1x(cpus=2, mem=8, m5.large, ...)          READY          
    http-server   4   2        -                           1 min ago    AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING   
    http-server   5   2        -                           1 min ago    AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING   


Once the total number of both old and new replicas exceeds the requested number, old replicas will be scaled down.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1,2        10m 4s  READY   3/5       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                 STATUS         
    http-server   1   1        http://54.173.203.169:8081  10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY          
    http-server   2   1        http://52.87.241.103:8081   10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY          
    http-server   3   2        http://3.93.241.163:8081    1 min ago    AWS (us-east-1b)     1x(cpus=2, mem=8, m5.large, ...)          READY          
    http-server   4   2        -                           1 min ago    AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING   
    http-server   5   2        -                           1 min ago    AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING   


Eventually, we will only have new replicas ready to serve user requests.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT
    http-server  2        11m 42s  READY   3/3       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED    INFRA                RESOURCES                                 STATUS  
    http-server   3   2        http://3.93.241.163:8081    3 mins ago  AWS (us-east-1b)     1x(cpus=2, mem=8, m5.large, ...)          READY   
    http-server   4   2        http://18.206.226.82:8081   3 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY   
    http-server   5   2        http://3.26.232.31:8081     1 min ago   AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY



.. _blue-green-update:

Blue-green update
------------------

SkyServe also supports blue-green updates, by the following command:

.. code-block:: console

    $ sky serve update --mode blue_green service-name new_service.yaml


In this update mode, SkyServe will launch new replicas described by ``new_service.yaml`` with the following behavior:

* An update is initiated, and traffic will continue to be redirected to existing (old) replicas.
* New replicas (with new settings) are brought up in the background.
* Traffic will be redirected to new replicas only when all new replicas are ready.
* Old replicas are scaled down after all new replicas are ready.


During an update, traffic is entirely serviced by either old-versioned or
new-versioned replicas.  :code:`sky serve status` shows the latest service
version and each replica's version.

Example
~~~~~~~

We use the same service ``http-server`` as an example. We can then use :code:`sky serve update --mode blue_green` to update the service:

.. code-block:: console

    $ sky serve update http-server --mode blue_green examples/serve/http_server/task.yaml


SkyServe will trigger launching three new replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  2        6m 15s  READY   2/5       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                 STATUS       
    http-server   1   1        http://54.173.203.169:8081  6 mins ago   AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY         
    http-server   2   1        http://52.87.241.103:8081   6 mins ago   AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          READY         
    http-server   3   2        -                           21 secs ago  AWS (us-east-1b)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING  
    http-server   4   2        -                           21 secs ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING  
    http-server   5   2        -                           21 secs ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          PROVISIONING  


When a new replica is ready, the traffic will still be redirected to old replicas.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  1        10m 4s  READY   3/5       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                   STATUS        
    http-server   1   1        http://54.173.203.169:8081  10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)           SHUTTING_DOWN  
    http-server   2   1        http://52.87.241.103:8081   10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)           READY          
    http-server   3   2        http://3.93.241.163:8081    1 min ago    AWS (us-east-1b)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY          
    http-server   4   2        http://18.206.226.82:8081   1 min ago    AWS (us-east-1a)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY          
    http-server   5   2        -                           1 min ago    AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)           PROVISIONING   


Once the total number of new replicas satisfies the requirements, traffics will be redirected to new replicas and old replicas will be scaled down.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME  STATUS  REPLICAS  ENDPOINT
    http-server  2        10m 4s  READY   3/5       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                 STATUS         
    http-server   1   1        http://54.173.203.169:8081  10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          SHUTTING_DOWN  
    http-server   2   1        http://52.87.241.103:8081   10 mins ago  AWS (us-east-1a)     1x(cpus=2, mem=8, m5.large, ...)          SHUTTING_DOWN  
    http-server   3   2        http://3.93.241.163:8081    1 min ago    AWS (us-east-1b)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY          
    http-server   4   2        http://18.206.226.82:8081   1 min ago    AWS (us-east-1a)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY          
    http-server   5   2        http://3.26.232.31:8081     1 min ago    AWS (us-east-1a)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY

Eventually, same as the rolling update, we will only have new replicas ready to serve user requests.

.. code-block:: console

    $ sky serve status http-server

    Services
    NAME         VERSION  UPTIME   STATUS  REPLICAS  ENDPOINT
    http-server  2        11m 42s  READY   3/3       44.206.240.249:30002

    Service Replicas
    SERVICE_NAME  ID  VERSION  ENDPOINT                    LAUNCHED     INFRA                RESOURCES                                 STATUS         
    http-server   3   2        http://3.93.241.163:8081    3 mins ago   AWS (us-east-1b)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY          
    http-server   4   2        http://18.206.226.82:8081   3 mins ago   AWS (us-east-1a)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY          
    http-server   5   2        http://3.26.232.31:8081     1 min ago    AWS (us-east-1a)     1x(cpus=4, mem=16, m5.xlarge, ...)        READY

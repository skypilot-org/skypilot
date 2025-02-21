.. _async:

Asynchronous Execution
======================

All SkyPilot interfaces (commands or APIs) sends asynchronous requests to the SkyPilot API server.
The output of a request is streamed back to the local client.

CLIs
----

For example, when a user runs ``sky launch -c my-cluster``, the following output is streamed to the terminal:

.. code-block:: console

    $ sky launch -c my-cluster --cpus 2
    Considered resources (1 node):
    ---------------------------------------------------------------------------------------------
    CLOUD        INSTANCE    vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN
    ---------------------------------------------------------------------------------------------
    Kubernetes   2CPU--2GB   2       2         -              in-cluster    0.00          ✔
    AWS          m6i.large   2       8         -              us-east-1     0.10
    ---------------------------------------------------------------------------------------------
    Launching a new cluster 'my-cluster'. Proceed? [Y/n]:
    ⚙︎ Launching on Kubernetes.
    └── Pod is up.
    ⠴ Preparing SkyPilot runtime (2/3 - dependencies)  View logs: sky api logs -l sky-2024-12-13-05-27-22-754475/provision.log


When a user interrupts the command with ``Ctrl+C``, the request will continue
running in the background on the server. The user can reattach to the logs of
the request with ``sky api logs``, or cancel the request with ``sky api cancel``.

.. code-block:: console

    $ sky launch -c my-cluster --cpus 2
    ...
    ^C
    ⚙︎ Request will continue running asynchronously.
    ├── View logs: sky api logs 73d316ac
    ├── Or, visit: http://127.0.0.1:46580/api/stream?request_id=73d316ac
    └── To cancel the request, run: sky api cancel 73d316ac


As a special case, terminating (``sky down my-cluster``) or stopping (``sky stop my-cluster``) a cluster will automatically cancel all existing requests on the cluster, including both ``PENDING`` and ``RUNNING`` requests.

.. note::

    Currently, ``sky jobs cancel`` and ``sky serve down`` do not abort other requests.

Running CLIs Asynchronously
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Most of SkyPilot CLIs support a ``--async`` flag, which will submit the request asynchronously and return immediately. With this
flag, you can submit multiple requests quickly without waiting for each request to complete.

.. code-block:: console

  $ for i in {1..10}; do sky jobs launch -n job-$i -y --async "echo hello SkyPilot $i"; done


See more for running many parallel jobs in :ref:`many-jobs`.
  
SDKs
----

Similar to the CLIs, the SkyPilot SDKs send asynchronous requests to the SkyPilot API server. When a SDK function is invoked, it will return a request ID, which can be used to stream the logs, wait for the request to finish, or cancel the request.

.. code-block:: python

  import sky
  task = sky.Task(run="echo hello SkyPilot", resources=sky.Resources(cloud=sky.AWS()))
  request_id = sky.launch(task, cluster_name="my-cluster")
  # Stream logs and get the output
  job_id, handle = sky.stream_and_get(request_id)
  # Tail the log of the job, which is a synchronous call
  sky.tail_logs(job_id)


Note that the following log functions are synchronous:

- ``sky.tail_logs()``
- ``sky.download_logs()``
- ``sky.jobs.tail_logs()``
- ``sky.jobs.download_logs()``
- ``sky.serve.tail_logs()``


.. note::
  
  If you upgraded SkyPilot from a version before 0.8.0, your program using SkyPilot SDKs needs to be updated to
  use the new `sky.stream_and_get` function to retrieve the result of a SDK function call. See the :ref:`migration guide <migration-0.8.0>` for more details.


Managing SkyPilot Requests
--------------------------

You can access the asynchronous SkyPilot requests you submitted through :ref:`sky api <sky-api-cli>` commands.


Listing requests
~~~~~~~~~~~~~~~~

To view all requests on the server, run ``sky api status``.

.. code-block:: console

    $ # List all ongoing requests
    $ sky api status
    ID                                    User             Name    Created         Status
    0d35ffa7-2813-4f3b-95c2-c5ab2238df50  user2            logs    a few secs ago  RUNNING
    a9d59602-b82b-4cf8-a10f-5cde4dd76f29  user1            launch  a few secs ago  RUNNING
    skypilot-status-refresh-daemon        skypilot-system  status  5 hrs ago       RUNNING

    $ # List all finished and ongoing requests
    $ sky api status -a

.. hint::

  ``sky api status`` shows the full ID for each request, but you can always use the prefix of
  the ID in ``sky api`` commands.

Stream Logs
~~~~~~~~~~~

To stream the logs of a request, run ``sky api logs <request-id>``.

.. code-block:: console

    $ sky api logs 0d35ffa7


Cancelling requests
~~~~~~~~~~~~~~~~~~~

To cancel requests, run ``sky api cancel <request-id> <request-id> ...``.

.. code-block:: console

    $ sky api cancel 0d35ffa7 a9d59602


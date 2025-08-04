.. _async:

Asynchronous Execution
======================

All SkyPilot CLIs and SDKs are asynchronous. When a CLI or SDK function is invoked,
a request will be sent to a :ref:`SkyPilot API server <api-server-simple>` and its logs will be streamed
back to the client.

Any request can be safely interrupted with ``Ctrl+C`` and the request
will continue running in the background on the API server.


.. _api-server-simple:

SkyPilot API server
---------------------

SkyPilot implements a client-server architecture.
When running locally, the first CLI or SDK call will automatically start a SkyPilot API server locally, and
any subsequent CLIs and SDKs will connect to the same API server.

An administrator can also :ref:`deploy a remote SkyPilot API server <sky-api-server>` for a team, so that multiple
users can share the same API server. Users can :ref:`connect to an API server <sky-api-server-connect>` via:

.. code-block:: console

  $ sky api login
  Enter the API server endpoint: http://1.2.3.4:30050

CLIs
----

For example, when a user runs ``sky launch -c my-cluster``, the following output is streamed to the terminal:

.. code-block:: console

    $ sky launch -c my-cluster --cpus 2
    Considered resources (1 node):
    ---------------------------------------------------------------------------------------------
    INFRA                   INSTANCE      vCPUs   Mem(GB)   GPUS      COST ($)   CHOSEN
    ---------------------------------------------------------------------------------------------
    Kubernetes (my-cluster) 2CPU--2GB     2       2         -         0.00       ✔
    AWS (us-east-1)         m6i.large     2       8         -         0.098     
    ---------------------------------------------------------------------------------------------
    Launching a new cluster 'my-cluster'. Proceed? [Y/n]:
    ⚙︎ Launching on Kubernetes.
    └── Pod is up.
    ⠴ Preparing SkyPilot runtime (2/3 - dependencies)  View logs: sky api logs -l sky-2024-12-13-05-27-22-754475/provision.log


When a user interrupts the command with ``Ctrl+C``, the request will continue
running in the background on the server.

The user can reattach to the logs of
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

Running CLIs asynchronously
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Most SkyPilot CLIs support a ``--async`` flag, which will submit the request asynchronously and return immediately. With this
flag, you can submit multiple requests quickly without waiting for each request to complete.

.. code-block:: console

  $ for i in {1..10}; do sky jobs launch -n job-$i -y --async "echo hello SkyPilot $i"; done


This is particularly useful for :ref:`launching many parallel jobs <many-jobs-scale-out>`.

Python SDK
----------

Similar to the CLIs, the SkyPilot SDK calls send asynchronous requests to the SkyPilot API server. When a SDK function is invoked, it will return a request ID, which can be used to stream the logs, wait for the request to finish, or cancel the request.

.. code-block:: python

  import sky
  task = sky.Task(
      run="echo hello SkyPilot", resources=sky.Resources(cloud=sky.AWS()))

  # sky.launch() returns a request ID.
  request_id = sky.launch(task, cluster_name="my-cluster")

  # Stream logs and get the output.
  job_id, handle = sky.stream_and_get(request_id)

  # Tail the logs of the job. This is a synchronous call.
  sky.tail_logs(job_id)


Async Python SDK
~~~~~~~~~~~~~~~~~

SkyPilot also provides an async SDK that automatically streams logs by default, providing a more interactive experience:

.. code-block:: python

  import asyncio
  from sky.client import sdk_async as sky
  import sky as sky_o

  async def main():
    task = sky_o.Task(
        run="echo hello SkyPilot")
    # Async functions stream logs by default and return results directly
    job_id, handle = await sky.launch(task, cluster_name="my-cluster")

    # Get cluster status with live streaming
    status = await sky.status()

    # Or disable streaming for simple result retrieval
    status = await sky.status(stream_logs=False)

  asyncio.run(main())


All async SDK functions support a ``stream_logs`` parameter:

- ``stream_logs=True`` (default): Live log streaming with interactive progress
- ``stream_logs=False``: Simple result retrieval without streaming

Note that the following log functions are synchronous:

- ``sky.tail_logs()``
- ``sky.download_logs()``
- ``sky.jobs.tail_logs()``
- ``sky.jobs.download_logs()``
- ``sky.serve.tail_logs()``


.. note::

  **Upgrading from v0.8 or older:** If you upgraded from a version equal to or
  older than 0.8.0 to any newer version,
  your program using SkyPilot SDKs needs to be updated to use the new
  |sky.stream_and_get|_ function to retrieve the result of a SDK function call.
  See the :ref:`migration guide <migration-0.8.1>` for more details.

.. https://stackoverflow.com/a/4836544
.. |sky.stream_and_get| replace:: :code:`sky.stream_and_get`
.. _sky.stream_and_get: ../reference/api.html#sky-stream-and-get

Managing requests
------------------------

You can access the asynchronous SkyPilot requests through |sky api|_ commands.

.. |sky api| replace:: :code:`sky api`
.. _sky api: ../reference/cli.html#sky-api-cli

List requests
~~~~~~~~~~~~~~

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

Stream logs
~~~~~~~~~~~

To stream the logs of a request, run ``sky api logs <request-id>``.

.. code-block:: console

    $ sky api logs 0d35ffa7


Cancel requests
~~~~~~~~~~~~~~~

To cancel requests, run ``sky api cancel <request-id> <request-id> ...``.

.. code-block:: console

    $ sky api cancel 0d35ffa7 a9d59602


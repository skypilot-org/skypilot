.. _sky-api-server:

SkyPilot API Server
==============================

SkyPilot implements a client-server architecture. When a user runs a command or an API call,
a SkyPilot client issues asynchronous requests to a SkyPilot API server, which
handles all requests.


Local API server (individual users)
-------------------------------------

For an individual user, SkyPilot can be used as a normal command line
tool. Whenever a SkyPilot command is run and an API server is not detected, SkyPilot will automatically start
a SkyPilot API server running locally in the background. No user action is needed.

.. image:: ../images/client-server/local.png
    :alt: SkyPilot API server local mode
    :align: center
    :width: 40%


Remote API server (multi-user organizations)
--------------------------------------------

For multi-user organizations, SkyPilot can be deployed as a remote
service. Multiple users in an organization can share the same
SkyPilot API server, so that users can:

1. Have a global view of all clusters, jobs, and services across users.
2. Manage and collaborate on clusters and jobs.
3. Interact with the same state from any new device.

**For admins**, see `SkyPilot API Server Deployment <https://github.com/assemble-org/prototype/blob/master/README.md>`__ on deploying an SkyPilot API server.

**For end users**, use :ref:`sky-api-login` to connect to the API server.

.. image:: ../images/client-server/remote.png
    :alt: SkyPilot API server remote mode
    :align: center
    :width: 50%


Asynchronous request execution
------------------------------

All SkyPilot client calls (commands or API calls) are sent to the SkyPilot API
server as asynchronous requests. The output of an request is streamed
back to the local client.

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
    ⠴ Preparing SkyPilot runtime (2/3 - dependencies)  View logs: sky api get -l sky-2024-12-13-05-27-22-754475/provision.log


When a user interrupts the command with ``Ctrl+C``, the request will continue
running in the background on the server. The user can reattach to the logs of
the request with ``sky api get``, or abort the request with ``sky api abort``.

.. code-block:: console

    $ sky launch -c my-cluster --cpus 2
    ...
    ^C
    ⚙︎ Request will continue running asynchronously.
    ├── View logs: sky api get f059d660-29c5-4f22-bd13-ee5d62d974c7
    ├── Or, visit: http://xx.xx.xx.xx:30050/stream?request_id=f059d660-29c5-4f22-bd13-ee5d62d974c7
    └── To abort the request, run: sky api abort f059d660-29c5-4f22-bd13-ee5d62d974c7


As a special case, terminating (``sky down my-cluster``) or stopping (``sky stop my-cluster``) a cluster will automatically abort all existing requests on the cluster, including both ``PENDING`` and ``RUNNING`` requests.

.. note::

    Currently, ``sky jobs cancel`` and ``sky serve down`` do not abort other requests.

API server cheatsheet
----------------------

Below are some common commands to interact with the API server.
See :ref:`sky-api-cli` for more details.


List all requests
~~~~~~~~~~~~~~~~~

To view all requests on the server, run ``sky api ls``.

.. code-block:: console

    $ # List all ongoing requests
    $ sky api ls
    ID                                    User             Name    Created         Status
    0d35ffa7-2813-4f3b-95c2-c5ab2238df50  user2            logs    a few secs ago  RUNNING
    a9d59602-b82b-4cf8-a10f-5cde4dd76f29  user1            launch  a few secs ago  RUNNING
    skypilot-status-refresh-daemon        skypilot-system  status  5 hrs ago       RUNNING

    $ # List all finished and ongoing requests
    $ sky api ls -a


Get API server URL and version
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To get the current API server info (URL and version), run ``sky api info``.

.. code-block:: console

    $ sky api info
    Using SkyPilot server: http://skypilot:alpha1@1.2.3.4:30050 (version: 1.0.0-dev0, commit: 6864695)


Stop and restart local API server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To stop the local API server, run ``sky api stop``.
To restart the local API server, run any SkyPilot command.

.. code-block:: console

    $ # Stop the local API server
    $ sky api stop
    $ # Any subsequent SkyPilot command will restart the local API server.

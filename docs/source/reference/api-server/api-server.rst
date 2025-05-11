.. _sky-api-server:

Team Deployment
===============

SkyPilot implements a client-server architecture. When a user runs a command or invokes a SDK,
a SkyPilot client submits :ref:`asynchronous requests <async>` to a **SkyPilot API server**, which
handles all requests.


Local API server (individual users)
-------------------------------------

For an individual user, SkyPilot can be used as a normal command line
tool. Whenever a SkyPilot command is run and an API server is not detected, SkyPilot will automatically start
a SkyPilot API server running locally in the background. No user action is needed.
And you can access the dashboard at `http://127.0.0.1:46580/dashboard` by default.

.. image:: ../../images/client-server/local.png
    :alt: SkyPilot API server local mode
    :align: center
    :width: 40%


Remote API server (multi-user teams)
------------------------------------

For multi-user teams, SkyPilot can be deployed as a remote
service. Multiple users in an organization can share the same
SkyPilot API server. The benefits include:

- **Deploy once & onboard seamlessly**: Set up one SkyPilot API server (in the cloud or on Kubernetes), and team members can onboard with a single endpoint.
- **Multi-tenancy**: Share clusters, jobs, and services securely among teammates.
- **Unified view and management**: Get a single view of all running clusters and jobs across the organization and all infra you have.
- **Fault-tolerant and cloud-native**: SkyPilot API server deployment is cloud-native and fully fault-tolerant, eliminating the risk of workload loss.
- **Integrate with workflow orchestrators**: Schedule workflows with orchestrators (like Airflow or Temporal), and let SkyPilot manage your diverse infrastructure.

.. image:: ../../images/client-server/remote.png
    :alt: SkyPilot API server remote mode
    :align: center
    :width: 50%


Getting started with a remote API server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. grid:: 1 1 2 2
    :gutter: 2

    .. grid-item-card::  👋 Connect to API Server
        :link: sky-api-server-connect
        :link-type: ref
        :text-align: center

        Already deployed the API server? Connect to it with ``sky api login``.

    .. grid-item-card::  ⚙️ Deploy SkyPilot API Server
        :link: sky-api-server-deploy
        :link-type: ref
        :text-align: center

        Follow these instructions to deploy the API server on your infrastructure.


.. _sky-api-server-connect:

Connecting to an API server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Once you have :ref:`deployed <sky-api-server-deploy>` the API server, you can configure your local SkyPilot
to connect to it.
We recommend using a Python 3.9 or 3.10 environment for the SkyPilot client. See `uv <https://docs.astral.sh/uv/>`_ or `conda <https://docs.conda.io/en/latest/>`_ for creating an environment with different python versions.

:ref:`Install <installation>` the SkyPilot client on your local machine:

.. code-block:: console

    $ pip install -U skypilot-nightly

Run ``sky api login`` to connect to the API server.

.. code-block:: console

    $ sky api login
    Enter your SkyPilot API server endpoint: http://skypilot:password@1.2.3.4:30050

This will save the API server endpoint to your ``~/.sky/config.yaml`` file.

To verify that the API server is working, run ``sky api info``:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: http://127.0.0.1:46580 Dashboard: http://127.0.0.1:46580/dashboard
    ├── Status: healthy, commit: xxxxx, version: 1.0.0-dev0
    └── User: skypilot-user (xxxxxx)


.. tip::

    You can also set the API server endpoint using the ``SKYPILOT_API_SERVER_ENDPOINT`` environment variable. It will override the value set in ``~/.sky/config.yaml``:

    .. code-block:: console

        $ export SKYPILOT_API_SERVER_ENDPOINT=http://skypilot:password@myendpoint.com:30050
        $ sky api info
        Using SkyPilot API server: http://myendpoint.com:30050 Dashboard: http://myendpoint.com:30050/dashboard
        ├── Status: healthy, commit: xxxxx, version: 1.0.0-dev0
        └── User: skypilot-user (xxxxxx)


.. toctree::
   :hidden:

   Deploying API Server <api-server-admin-deploy>
   Upgrading API Server <api-server-upgrade>
   Performance Best Practices <api-server-tunning>
   Troubleshooting <api-server-troubleshooting>

By default, each user connected to the API server will only see their own resources.


To see other users' clusters and the job/serve controllers, use the ``-u`` flag.

.. code-block:: console
    :emphasize-lines: 5,7,14

    $ sky status -u
    Clusters
    NAME                          USER        LAUNCHED      RESOURCES                         STATUS   AUTOSTOP  COMMAND
    my-cluster-2                  my-user     2 hrs ago     1x GCP(n2-standard-8)             STOPPED  -         sky launch task-2.yaml
    other-cluster                 other-user  1 week ago    1x AWS(m6i.16xlarge)              UP       -         sky launch --cloud aws...
    my-cluster-1                  my-user     2 months ago  1x AWS(m6i.4xlarge)               STOPPED  -         sky launch task-1.yaml
    sky-jobs-controller-7c3d4ff7  root        2 days ago    1x AWS(r6i.xlarge, disk_size=50)  STOPPED  10m       sky jobs launch --env PART...

    $ sky jobs queue -u
    Fetching managed job statuses...
    Managed jobs
    ID  TASK  NAME       USER        RESOURCES  SUBMITTED   TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
    3   -     job-2      my-user     1x[CPU:2]  2 days ago  2m 10s         1m 14s        0            CANCELLED
    2   -     other-job  other-user  1x[CPU:2]  2 days ago  11m 54s        10m 52s       0            CANCELLED
    1   -     job-1      my-use      1x[CPU:2]  5 days ago  1m 7s          3s            0            SUCCEEDED

.. _sky-api-server-in-docker:

Deploy SkyPilot API Server in Docker
=====================================

You can run the SkyPilot API server in a Docker container to share it with multiple users on a single machine. This is useful when you want to provide centralized access to SkyPilot in organizations that already have a single login node for everyone.

Prerequisites
-------------

Before you begin, make sure the following requirements are met on the machine:

* :ref:`SkyPilot is installed and cloud credentials are setup<installation>` for the user who will run the API server
* Docker is installed and the docker daemon is running

Deploy the API server
---------------------

Find the available versions of the API server image in our `stable channel <https://pypi.org/project/skypilot/#history>`_ or `nightly channel <https://pypi.org/project/skypilot-nightly/#history>`_.

Then, run the following command to deploy the API server:

.. code-block:: bash

    # Replace the image and port with the desired release version
    IMAGE=berkeleyskypilot/skypilot:0.10.0
    PORT=8080
    # Ensure the local API server is stopped
    sky api stop
    touch ~/.api-server-config.yaml
    # Or use a nightly version
    # IMAGE=berkeleyskypilot/skypilot-nightly:1.0.0.dev20250718
    docker run -d \
     --name skypilot-api-server \
     -p $PORT:46580 \
     -v $HOME:/root \
     -e SKYPILOT_GLOBAL_CONFIG=/root/.api-server-config.yaml \
     --entrypoint tini \
     $IMAGE \
     -- sky api start --deploy --foreground

.. dropdown:: Commands and flags explaination

    * ``sky api stop``:  SkyPilot starts an implicit local API server in background when the user runs commands like ``sky launch``. This command is to stop the implicit local API server (if any) to avoid conflicts with the API server in the container.
    * ``-d``: Run the SkyPilot API server container in the background
    * ``--name skypilot-api-server``: Give the container a name so that it can be easily identified.
    * ``-p $PORT:46580``: Map the ``$PORT`` on host to the port 46580 of the container so that the API server can be accessed from the host. ``$PORT`` can be any port numbers other than 46580.
    * ``-v $HOME:/root``: Mount the home directory of current user to the container so that:
      * The API server can use the cloud credentials of the current user;
      * The state of API server can be persisted on the host;
    * ``-e SKYPILOT_GLOBAL_CONFIG=/root/.api-server-config.yaml``: Override the default :ref:`configuration file<config-yaml>` from ``~/.sky/config.yaml`` to ``~/.api-server-config.yaml`` in the container to avoid conflicts between the server config and the user config. The configuration set via dashboard will also be persisted to this file on the host machine;
    * ``--entrypoint tini``: Run the container with tini as the entrypoint to handle the signals;
    * ``-- sky api start --deploy --foreground``: Start the API server in the container.

After the container is started, you can monitor the logs of the API server using the following command:

.. code-block:: bash

    docker logs -f skypilot-api-server

Connect to the API server
-------------------------

Run the following command to connect to the API server:

.. code-block:: bash

    sky api login -e http://localhost:$PORT

.. note::

    By default, all the users on the machine will have access to the API server.


Upgrade the API server
----------------------

Docker image is immutable. To upgrade the API server, you need to stop and remove the existing container, then start a new container using the updated image.

.. code-block:: bash

    # Replace the image with the desired version to upgrade
    IMAGE=berkeleyskypilot/skypilot-nightly:1.0.0.dev20250718
    # Pull the image before stopping the container to reduce the downtime
    docker pull $IMAGE
    docker stop skypilot-api-server
    docker rm skypilot-api-server
    # Just the same command with a new image, keep it consistent with the original deploy command if you made any changes at deploy time
    docker run -d \
     --name skypilot-api-server \
     -p $PORT:46580 \
     -v $HOME:/root \
     -e SKYPILOT_GLOBAL_CONFIG=/root/.api-server-config.yaml \
     --entrypoint tini \
     $IMAGE \
     -- sky api start --deploy --foreground
   
.. note::

    Upgrade must be done by the user who deployed the API server to ensure the home directory is consistent.

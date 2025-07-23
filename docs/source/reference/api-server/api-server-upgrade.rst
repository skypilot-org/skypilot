.. _sky-api-server-upgrade:

Upgrading SkyPilot API Server
=============================

This page provides an overview of the steps you should follow to upgrade a remote SkyPilot API server:

* :ref:`sky-api-server-helm-upgrade`
* :ref:`sky-api-server-vm-upgrade`

.. _sky-api-server-helm-upgrade:

Upgrade API server deployed with Helm
-------------------------------------

With :ref:`Helm deployement <sky-api-server-deploy>`, it is possible to :ref:`upgrade the SkyPilot API server gracefully<sky-api-server-graceful-upgrade>` without causing client-side error with the steps below.

Step 1: Prepare an upgrade
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Find the version to use in SkyPilot `nightly build <https://pypi.org/project/skypilot-nightly/#history>`_.
2. Update SkyPilot helm repository to the latest version:

.. code-block:: bash

    helm repo update skypilot

3. Prepare versioning environment variables.  ``NAMESPACE`` and ``RELEASE_NAME`` should be set to the currently installed namespace and release:

.. code-block:: bash

    NAMESPACE=skypilot # TODO: change to your installed namespace
    RELEASE_NAME=skypilot # TODO: change to your installed release name
    VERSION=1.0.0-dev20250410 # TODO: change to the version you want to upgrade to
    IMAGE_REPO=berkeleyskypilot/skypilot-nightly

Step 2: Upgrade the API server and clients
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upgrade the clients:

.. code-block:: bash

    pip install -U skypilot-nightly==${VERSION}

Upgrade the API server:

.. code-block:: bash

    # --reuse-values is critical to keep the values set in the previous installation steps.
    helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.image=${IMAGE_REPO}:${VERSION}

When the API server is being upgraded, the SkyPilot CLI and Python SDK will automatically retry requests until the new version of the API server is started. So the upgrade process is graceful if the new version of the API server does not break :ref:`API compatbility<sky-api-server-api-compatibility>`. For more details, refer to :ref:`sky-api-server-graceful-upgrade`.

Optionally, you can watch the upgrade progress with:

.. code-block:: console

    $ kubectl get pod --namespace $NAMESPACE -l app=${RELEASE_NAME}-api --watch
    NAME                                       READY   STATUS            RESTARTS   AGE
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:0/2          0          7s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:1/2          0          24s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     PodInitializing   0          26s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Running           0          27s
    skypilot-demo-api-server-cf4896bdf-62c96   1/1     Running           0          50s

The upgraded API server is ready to serve requests after the pod becomes running and the ``READY`` column shows ``1/1``.

.. note::

    ``apiService.config`` will be IGNORED during an upgrade. To update your SkyPilot config, see :ref:`here <sky-api-server-config>`.


Step 3: Verify the upgrade
~~~~~~~~~~~~~~~~~~~~~~~~~~

Verify the API server is able to serve requests and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <ENDPOINT>
    ├── Status: healthy, commit: 022a5c3ffe258f365764b03cb20fac70934f5a60, version: 1.0.0.dev20250410
    └── User: aclice (abcd1234)

If possible, you can also trigger your pipelines that depend on the API server to verify there is no compatibility issue after the upgrade.

.. _sky-api-server-vm-upgrade:

Upgrade the API server deployed on VM
-------------------------------------

.. note::

    VM deployment does not offer graceful upgrade. We recommend the Helm deployment :ref:`sky-api-server-deploy` in production environments. The following is a workaround for upgrading SkyPilot API server in VM deployments.

Suppose the cluster name of the API server is ``api-server`` (which is used in the :ref:`sky-api-server-cloud-deploy` guide), you can upgrade the API server with the following steps:

1. Get the version to upgrade to from SkyPilot `nightly build <https://pypi.org/project/skypilot-nightly/#history>`_.

2. Switch to the original API server endpoint used to launch the cloud VM for API server. It is usually locally started when you ran ``sky launch -c api-server skypilot-api-server.yaml`` in :ref:`sky-api-server-cloud-deploy` guide:

.. code-block:: bash

    # Replace http://localhost:46580 with the real API server endpoint if you were not using the local API server to launch the API server VM instance.
    sky api login -e http://localhost:46580

3. Check the API server VM instance is ``UP``:

.. code-block:: console

    $ sky status api-server
    Clusters
    NAME        LAUNCHED     RESOURCES                                                                  STATUS  AUTOSTOP  COMMAND
    api-server  41 mins ago  1x AWS(c6i.2xlarge, image_id={'us-east-1': 'docker:berkeleyskypilot/sk...  UP      -         sky exec api-server pip i...

4. Upgrade the clients:

.. code-block:: bash

    pip install -U skypilot-nightly==${VERSION}

.. note::

    After upgrading the clients, they should not be used until the API server is upgraded to the new version.

5. Upgrade the SkyPilot on the VM and restart the API server:

.. note::

    Upgrading and restarting the API server will interrupt all pending and running requests.

.. code-block:: bash

    sky exec api-server "pip install -U skypilot-nightly[all] && sky api stop && sky api start --deploy"
    # Alternatively, you can also upgrade to a specific version with:
    sky exec api-server "pip install -U skypilot-nightly[all]==${VERSION} && sky api stop && sky api start --deploy"

6. Switch back to the remote API server:

.. code-block:: bash

    ENDPOINT=$(sky status --endpoint api-server)
    sky api login -e $ENDPOINT

7. Verify the API server is running and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <ENDPOINT>
    ├── Status: healthy, commit: 022a5c3ffe258f365764b03cb20fac70934f5a60, version: 1.0.0.dev20250410
    └── User: aclice (abcd1234)

.. _sky-api-server-graceful-upgrade:

Graceful upgrade
----------------

A server can be gracefully upgraded when the following conditions are met:

* :ref:`Helm deployment<sky-api-server-deploy>` is used;
* Versions before and after upgrade are :ref:`compatible<sky-api-server-api-compatibility>`;

Behavior when the API server is being upgraded:

* For critical ongoing requests (e.g., launching a cluster), it waits for them to finish with a timeout.
* For non-critical ongoing requests (e.g., log tailing), it cancels them and returns an error to ask the client to retry.
* For new requests, it returns an error to ask the client to retry.

SkyPilot Python SDK and CLI will automatically retry until the new version of API server starts, and ongoing requests (e.g., log tailing) will automatically resume:

.. image:: https://i.imgur.com/jUjXu0J.gif
  :alt: GIF for graceful upgrade
  :align: center

To ensure that all the regular critical requests can complete within the timeout, you can adjust the timeout by setting :ref:`apiService.terminationGracePeriodSeconds <helm-values-apiService-terminationGracePeriodSeconds>` in helm values based on your workload, e.g.:

.. code-block:: bash

    helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.terminationGracePeriodSeconds=300

.. _sky-api-server-api-compatibility:

API compatbility
----------------

SkyPilot maintain an internal API version which will be bumped when an incompatible API change is introduced. Client and server can only communicate when they run on the same API version.

The version strategy of SkyPilot follows the following API compatbility guarantees:

* The API version will not be bumped within a minor version, i.e. upgrading patch version is guaranteed to be compatible;
* The API version might be bumped between minior versions, i.e. upgrading minior version should be treated as operation that breaks API compatibility;
* There is no guarantee about the API version in the nightly build;

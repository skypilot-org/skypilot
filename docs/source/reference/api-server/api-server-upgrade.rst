.. _sky-api-server-upgrade:

Upgrading SkyPilot API Server
=============================

This page provides an overview of the steps you should follow to upgrade a remote SkyPilot API server:

* :ref:`sky-api-server-helm-upgrade`
* :ref:`sky-api-server-vm-upgrade`

.. _sky-api-server-helm-upgrade:

Upgrade API server deployed with Helm
-----------------------------------------

Here we introduce the steps for upgrading a remote API server deployed with :ref:`Helm deployement <sky-api-server-deploy>`.

.. note::

    Upgrading the API server introduces downtime. We recommend scheduling the upgrade during a **maintenance window**: cordon and drain the old API server, then perform the upgrade.

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

.. note::

    To minimize the impact of upgrading, you can :ref:`cordon and drain the API server <cordon-drain-api-server>` before performing this step. The optional cordon and drain step enforces that all requests be finished before the upgrade and there is no new request during the upgrade.
    
    If you choose not to perform cordon and drain, upgrade has the following behaviors:
    
    * Upgrading the API server will interrupt any pending and ongoing requests on the API server, e.g., ongoining ``sky launch``. Clients can recover ongoing requests by running the same commands again after the new API server starts.
    * Upgrading only the server or only the client may break compatibility between them. In this case, an error will be raised on the client side, with a hint to upgrade the client to the same version as the server.

Upgrade the clients:

.. code-block:: bash

    pip install -U skypilot-nightly==${VERSION}

Upgrade the API server:

.. code-block:: bash

    # --reuse-values is critical to keep the values set in the previous installation steps.
    helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.image=${IMAGE_REPO}:${VERSION}

Optionally, you can watch the upgrade progress with:

.. code-block:: console

    $ kubectl get pod -l app=${RELEASE_NAME}-api --watch
    NAME                                       READY   STATUS     RESTARTS   AGE
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:0/2   0          7s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:1/2   0          24s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     PodInitializing   0          26s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Running           0          27s
    skypilot-demo-api-server-cf4896bdf-62c96   1/1     Running           0          50s

The upgraded API server is ready to serve requests after the pod becomes running and the ``READY`` column shows ``1/1``. If the API server was cordoned previously, the cordon will be removed automatically after the upgrade.

Step 3: Verify the upgrade
~~~~~~~~~~~~~~~~~~~~~~~~~~

Verify the API server is able to serve requests and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <ENDPOINT>
    ├── Status: healthy, commit: 022a5c3ffe258f365764b03cb20fac70934f5a60, version: 1.0.0.dev20250410
    └── User: aclice (abcd1234)

If possible, you can also trigger your pipelines that depend on the API server to verify there is no compatibility issue after the upgrade.

.. _cordon-drain-api-server:

Optional: Cordon and drain the API server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


The following steps ensure graceful upgrade of the API server: (1) Reject new request to the API server (cordon), and (2) Wait for all existing requests to finish on the old API server (drain) during the maintenance window.

.. note:: 
  It requires ``patch`` and ``exec`` (or ``port-forward``) access to the API server Pod.

1. Cordon SkyPilot API server to reject new requests:

.. code-block:: bash

    kubectl get pod -l app=${RELEASE_NAME}-api -oname | xargs kubectl patch --type merge -p '{"metadata": {"labels": {"skypilot.co/ready": null}}}'
    
.. note::
    All new requests will be rejected by the Ingress after this step. Make sure there is no critical service depending on the API server before proceeding.

2. Verify the API server is cordoned, you should see the following error:

.. code-block:: console

    $ sky api info
    sky.exceptions.ApiServerConnectionError: Could not connect to SkyPilot API server at <ENDPOINT>. Please ensure that the server is running. Try: curl <ENDPIONT>

.. dropdown:: Resolve cordon failure for early nightly release

    If you are upgrading from an early nightly build that does not support cordoning (``sky api info`` will succeed), you can manually enable cordon support by running:

    .. code-block:: bash

        kubectl patch service ${RELEASE_NAME}-api-service -p '{"spec":{"selector":{"skypilot.co/ready":"true"}}}'
    
    After the patch, verify the API server is cordoned again.

3. Drain the old API server by waiting for all current requests to finish, or canceling them:

.. tab-set::

    .. tab-item:: Inspecting requests

        You can inspect the status of requests by running:

        .. code-block:: console

            $ kubectl get po -l app=${RELEASE_NAME}-api -oname | xargs -I {} kubectl exec {} -c skypilot-api -- sky api status
            sky api status
            ID                                    User             Name        Created         Status
            942f6ab3-f5b6-4a50-acd6-0e8ad64a3ec2  <USER>           sky.launch  a few secs ago  PENDING
            8c5f19ca-513c-4068-b9c9-d4b7728f46fb  <USER>           sky.logs    26 secs ago     RUNNING
            skypilot-status-refresh-daemon        skypilot-system  sky.status  25 mins ago     RUNNING

        .. note::

            The ``skypilot-status-refresh-daemon`` is a background process managed by API server that is never stopped. Also, ``sky.logs`` can last for a long time. Both of them can be safely interrupted.
    
    .. tab-item:: Canceling requests

        You can cancel less critical requests by running:

        .. code-block:: console

            $ kubectl get po -l app=${RELEASE_NAME}-api -oname | xargs -I {} kubectl exec {} -c skypilot-api -- sky api cancel ${ID}

.. dropdown:: Using port-forward to access the API server

    If you do not have ``exec`` access to the API server Pod, you can also use ``port-forward`` to access the api status:

    .. code-block:: console

        $ kubectl get po -l app=${RELEASE_NAME}-api -oname | xargs -I {} kubectl port-forward {} 46580:46580 > /tmp/port-forward.log 2>&1 &
        $ PORT_FORWARD_PID=$!
        $ sky api login -e http://127.0.0.1:46580
        # Polling the status
        $ sky api status
        # Cancel less critical requests if needed
        $ sky api cancel ${ID}
        # Stop the port-forward after you are satisfied with the status
        $ kill $PORT_FORWARD_PID

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

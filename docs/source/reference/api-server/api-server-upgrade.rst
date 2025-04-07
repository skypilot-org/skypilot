.. _sky-api-server-upgrade:

Upgrading SkyPilot API Server
=============================

This page provides an overview of the steps you should follow to upgrade a remote SkyPilot API server deployed by Helm with the :ref:`sky-api-server-deploy` guide. For API server deployed with :ref:`sky-api-server-cloud-deploy`, refer to :ref:`sky-api-server-vm-upgrade`.

.. note::

    Upgrading the API server introduces downtime. We recommend to schedule the upgrade during a maintenance window. If you have a staging environment, you can upgrade the staging environment first to make sure the upgrade is successful before upgrading the production environment.

Prerequisites
-------------

* Complete :ref:`sky-api-server-deploy` to deploy the API server.

Step 1: Prepare an upgrade
--------------------------

1. Get the image you want to upgrade to from SkyPilot dockerhub repository: `nightly release <https://hub.docker.com/r/berkeleyskypilot/skypilot-nightly/tags>`_. A specific tag other than ``latest`` is required to make the upgrade process predictable.
2. Update SkyPilot helm repository to the latest version:

.. code-block:: bash

    helm repo update skypilot

3. Prepare variables for the upgrade, the ``NAMESPACE`` and ``RELEASE_NAME`` should be the same as the previous deployment step:

.. code-block:: bash

    NAMESPACE=<installed-namespace>
    RELEASE_NAME=<installed-release-name>
    # e.g. IMAGE=berkeleyskypilot/skypilot-nightly:1.0.0-dev.20250406
    IMAGE=<image-from-dockerhub>

Step 2: Cordon the API server
------------------------------------------

To minimize the impact of the downtime introduced by the upgrade, we recommend condon the API server before upgrading the API server. The following steps require `patch` and `exec` (or `port-forward`) access to the API server Pod.

1. Patch the API server to remove it from the Ingress backend:

.. note::

    All new requests will be rejected by the Ingress after this step. Make sure there is no critical service depending on the API server before proceeding.

.. code-block:: bash

    kubectl get pod -l app=${RELEASE_NAME}-api -oname | xargs kubectl patch --type merge -p '{"metadata": {"labels": {"skypilot.co/ready": null}}}'

2. Verify the API server is cordoned, you should see the following error:

.. code-block:: console

    $ sky api info
    sky.exceptions.ApiServerConnectionError: Could not connect to SkyPilot API server at <URL>. Please ensure that the server is running. Try: curl <URL>

.. dropdown:: Resolve cordon failure for early 0.8.0 nightly release

    If you are upgrading from early 0.8.0 nightly release that does not support cordoning (``sky api info`` will succeed), you can manually enable cordon support by running:

    .. code-block:: bash

        kubectl patch service ${RELEASE_NAME}-api-service -p '{"spec":{"selector":{"skypilot.co/ready":"true"}}}'
    
    After the patch, verify the API server is cordoned again.

3. For requests that have been sent to the API server, check the status of the requests and wait for the critical ones to finish:

.. code-block:: console

    $ kubectl get po -l app=${RELEASE_NAME}-api -oname | xargs -I {} kubectl exec {} -c skypilot-api -- sky api status
    sky api status
    ID                                    User             Name        Created         Status
    942f6ab3-f5b6-4a50-acd6-0e8ad64a3ec2  <USER>           sky.launch  a few secs ago  PENDING
    8c5f19ca-513c-4068-b9c9-d4b7728f46fb  <USER>           sky.logs    26 secs ago     RUNNING
    skypilot-status-refresh-daemon        skypilot-system  sky.status  25 mins ago     RUNNING

The `skypilot-status-refresh-daemon` is a background process managed by API server that can be safely interrupted. For other requests, you can either wait for a request to finish or cancel a request by running:

.. code-block:: console

    $ kubectl get po -l app=${RELEASE_NAME}-api -oname | xargs -I {} kubectl exec {} -c skypilot-api -- sky api cancel ${ID}

If you do not have `exec` access to the API server Pod, you can also use `port-forward` to access the api status:

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

Step 3: Upgrade SkyPilot clients
--------------------------------

Currently, compatibility between SkyPilot clients and server is only guaranteed when both sides are running the same version. We recommend upgrading the clients in autonomous pipelines to the same version as the API server before upgrading the API server to avoid breaking the pipeline after the upgrade. The image tag of API server is consistent with the pip package version, so you can upgrade the clients to the same version as the API server by running:

.. code-block:: bash

    # For nightly release. Keep the CLOUD_LIST in sync with the previous installation.
    pip install -U "skypilot-nightly[${CLOUD_LIST}]"==${IMAGE_TAG}

For clients used by developers, it is okay to upgrade to the same version as the API server either before or after the upgrade. Because if there is a compatibility issue, an error will raised to the developer with the upgrade command prompted.

Step 4: Upgrade the API server
------------------------------

Once all the critical requests have been finished and the clients in autonomous pipelines have been upgraded, you can upgrade the API server with the following command:

.. code-block:: bash

    # --reuse-values is critical to keep the values set in the previous installation steps.
    helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set apiService.image=${IMAGE}

Optionally, you can watch the upgrade progress with:

.. code-block:: console

    $ kubectl get pod -l app=${RELEASE_NAME}-api --watch
    NAME                                       READY   STATUS     RESTARTS   AGE
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:0/2   0          7s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:0/2   0          21s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:1/2   0          23s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Init:1/2   0          24s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     PodInitializing   0          26s
    skypilot-demo-api-server-cf4896bdf-62c96   0/1     Running           0          27s
    skypilot-demo-api-server-cf4896bdf-62c96   1/1     Running           0          50s

The upgraded API server is ready to serve requests after the pod is running and the ``READY`` column shows ``1/1``. The cordon will be removed automatically after the upgrade.

Step 5: Verify the upgrade
--------------------------

Verify the API server is able to serve requests and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <URL>
    ├── Status: healthy, commit: <COMMIT>, version: <VERSION>
    └── User: <USER> (<USER_ID>)

If possible, you can also trigger your pipelines that depend on the API server to verify there is no compatibility issue after the upgrade.

.. _sky-api-server-vm-upgrade:

Upgrade the API server deployed on VM
-------------------------------------

.. note::

    VM deployment does not offer graceful upgrading support. We recommend to use Helm deployment :ref:`sky-api-server-deploy` in production environments.

Suppose the cluster name of the API server is ``api-server`` (which is used in the :ref:`sky-api-server-cloud-deploy` guide), you can upgrade the API server with the following steps:

1. Switch to the original API server that is used to launch the API server VM instance. It is usually the local API server that was launched automatically when you ran ``sky launch`` to launch the API server VM instance in the :ref:`sky-api-server-cloud-deploy` guide:

.. code-block:: bash

    # Replace http://localhost:46580 with the real API server endpoint if you were not using the local API server to launch the API server VM instance.
    sky api login -e http://localhost:46580

2. Check the API server VM instance is ``UP``:

.. code-block:: console

    $ sky status --no-show-managed-jobs --no-show-services
    Clusters
    NAME        LAUNCHED     RESOURCES                                                                  STATUS  AUTOSTOP  COMMAND
    api-server  41 mins ago  1x AWS(c6i.2xlarge, image_id={'us-east-1': 'docker:berkeleyskypilot/sk...  UP      -         sky exec api-server pip i...

3. Upgrade the SkyPilot pip package on the VM and restart the API server:

.. note::

    Upgrade and restart the API server will interrupt all running requests and may cause compatibility issue that requires upgrading the clients to resolve.

.. code-block:: bash

    sky exec api-server "pip install -U skypilot-nightly[all] && sky api stop && sky api start --deploy"
    # Alternatively, you can also upgrade to a specific version with:
    sky exec api-server "pip install -U skypilot-nightly[all]==<target-version> && sky api stop && sky api start --deploy"

4. Switch back to the remote API server:

.. code-block:: bash

    sky api login -e http://$(sky status --endpoint 46580 api-server)

5. Verify the API server is running and the version is consistent with the version you upgraded to:

.. code-block:: console

    $ sky api info
    Using SkyPilot API server: <URL>
    ├── Status: healthy, commit: <COMMIT>, version: <VERSION>
    └── User: <USER> (<USER_ID>)

.. _api-server-persistence-example:

Persisting API server state when using Helm deployment
======================================================

This example shows how a helm-deployed API server's state can be persisted using an external persistent disk.

In this example, a SkyPilot API server is deployed using the Helm chart on a GKE cluster.
This server's state is persisted with a GCP Persistent Disk, such that the server's state is not lost even if the GKE cluster is torn down.

Prerequisites
-------------

* A GKE cluster with LoadBalancer or NodePort service support
* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/#kubectl>`_
* `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_
* [Optional] Lambda Cloud credentials

.. _api-server-persistence-example-create-disk:

Create a persistent disk on GCP
-------------------------------

First, create a persistent disk on GCP. This disk is used to persist the API server's state.

.. code-block:: bash

   export ZONE=us-east5-b
   export DISK_SIZE=10G
   export DISK_NAME=sky-api-server-state
   # This variable will be used later in the example.
   export PV_CLASS_NAME=sky-api-server-pv-class
   gcloud compute disks create $DISK_NAME --zone=$ZONE --size $DISK_SIZE

Note the ``$ZONE`` variable must match the zone of the GKE cluster.

.. _api-server-persistence-example-create-pv:

Create a persistent volume on GKE
---------------------------------

Next, create a persistent volume on GKE that uses the persistent disk that was just created.

``sky-pv.yaml``:

.. code-block:: yaml

    apiVersion: v1
    kind: PersistentVolume
    metadata:
        name: sky-api-server-pv
    spec:
        storageClassName: $PV_CLASS_NAME
        capacity:
            storage: $DISK_SIZE
        accessModes:
            - ReadWriteOnce
        csi:
            driver: pd.csi.storage.gke.io
            volumeHandle: projects/$PROJECT/zones/$ZONE/disks/$DISK_NAME
            fsType: ext4

Replace the variables in the above YAML.

Note the ``$PROJECT`` and ``$ZONE`` variables must match the project and zone of the GKE cluster.
In addition, ``$DISK_SIZE`` and ``$DISK_NAME`` must match the size and name of the persistent disk created on GCP.

Apply the Persistent Volume to the GKE cluster.

.. code-block:: bash

    kubectl apply -f sky-pv.yaml


.. _api-server-persistence-example-add-cloud-credentials:

[Optional] Add lambda cloud credentials to the API server
---------------------------------------------------------

Next, add cloud credentials to the API server.

In this example, the API server is configured to use Lambda Cloud credentials.

To learn how to configure the credentials for another cloud provider, please refer to :ref:`sky-api-server-configure-credentials`.

.. code-block:: bash

    export NAMESPACE=skypilot
    kubectl create namespace $NAMESPACE
    kubectl create secret generic lambda-credentials \
    --namespace $NAMESPACE \
    --from-literal api_key=YOUR_API_KEY


.. _api-server-persistence-example-deploy-api-server:

Deploy the API server using Helm
--------------------------------

Next, deploy the API server using Helm with the following command.

.. code-block:: bash

    # NAMESPACE is the namespace to deploy the API server in
    export NAMESPACE=skypilot
    # RELEASE_NAME is the name of the helm release, must be unique within the namespace
    export RELEASE_NAME=skypilot
    # Replace with your username and password to configure the basic auth credentials for the API server
    export WEB_USERNAME=skypilot
    export WEB_PASSWORD=yourpassword
    export AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
    # Deploy the API server
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
    --namespace $NAMESPACE \
    --create-namespace \
    --set ingress.authCredentials=$AUTH_STRING \
    --set storage.storageClassName=$PV_CLASS_NAME \
    --set storage.size=$DISK_SIZE \
    --set lambdaCredentials.enabled=true \
    --set lambdaCredentials.lambdaSecretName=lambda-credentials

Once the API server is deployed, find the API server URL following the instructions in :ref:`sky-get-api-server-url` to get the API server URL, then log in to the API server using the following command:

.. code-block:: bash

    sky api login --endpoint $API_SERVER_URL


If Lambda Cloud credentials are configured, check that Lambda Cloud is working by running:

.. code-block:: bash

    sky check

and verify that the list of enabled clouds include Lambda Cloud.


.. _api-server-persistence-example-create-cluster:

Create a SkyPilot cluster with the API server
---------------------------------------------

Now, create a cluster with the API server.

.. code-block:: bash

    sky launch --name my-cluster --cloud lambda


.. _api-server-persistence-example-delete-cluster:

Delete and recreate the GKE cluster
-----------------------------------

To simulate a catastrophic failure of the GKE cluster, delete the GKE cluster and recreate it.

Then, re-run the following sections:

- :ref:`api-server-persistence-example-create-pv`
- :ref:`api-server-persistence-example-add-cloud-credentials`
- :ref:`api-server-persistence-example-deploy-api-server`

The new API server URL is different from the previous URL, so run ``sky api login`` again with the new server URL.

.. _api-server-persistence-example-verify-state:

Verify the API server retains its state
---------------------------------------

Verify the API server retains its state by checking the cluster status.

.. code-block:: bash

    sky status

Since the API server retains its state, the cluster created from :ref:`api-server-persistence-example-create-cluster` is visible.

.. _api-server-persistence-example-cleanup:

Cleanup
-------

Delete the cluster created from :ref:`api-server-persistence-example-create-cluster`.

.. code-block:: bash

    sky down -a

Delete GKE cluster used for the exercise.

Delete the persistent disk on GCP.

.. code-block:: bash

    gcloud compute disks delete $DISK_NAME --zone=$ZONE

.. _api-server-persistence-example-conclusion:

Conclusion
----------

This example demonstrates how a PersistentVolume can be used to persist the API server's state.

While this example uses a GKE cluster with a GCP persistent disk as a backing volume,
the same can be done with other cloud providers that provide a CSI provider to a persistent block storage device.

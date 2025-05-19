.. _api-server-ha:

Advanced API Server High Availability across GKE Clusters
=========================================================

When a SkyPilot API server is :ref:`deployed using a Helm chart <sky-api-server-helm-deploy-command>`,
the API server is deployed with states persisted through a PVC in the Kubernetes cluster.

However, if you need to make the server resilient against Kubernetes cluster failures, you can further persist the API server states using cloud services,
such as a `GCP persistent volume <https://cloud.google.com/compute/docs/disks/persistent-disks>`_ .

By configuring a persistent disk to back the SkyPilot state, the SkyPilot API server becomes resilient to catastrophic k8s cluster failures, including a full cluster deletion.

To recover all SkyPilot state on a new cluster, simply:

1. Create the cloud credential secrets
2. Create the persistent volume definition
3. Deploy API server from the helm chart, specifying the same persistent volume.

The following is an end-to-end instruction for setting up a GKE cluster and a persistent volume to create a high-availability API server.

Prerequisites
-------------

* `A GKE cluster <https://cloud.google.com/kubernetes-engine/docs/how-to/creating-a-zonal-cluster>`_
* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/#kubectl>`_
* `gcloud CLI <https://cloud.google.com/sdk/docs/install>`_

.. _api-server-ha-create-disk:

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

.. _api-server-ha-create-pv:

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

    $ kubectl apply -f sky-pv.yaml

Then, verify that the persistent volume is created with the correct retention policy:

.. code-block:: bash

    $ kubectl get persistentvolume/sky-api-server-pv
    NAME                CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS      CLAIM   STORAGECLASS              VOLUMEATTRIBUTESCLASS   REASON   AGE
    sky-api-server-pv   10G        RWO            Retain           Available           sky-api-server-pv-class   <unset>                          18s

The ``RECLAIM POLICY`` should be set to ``Retain``.

.. _api-server-ha-deploy-api-server:

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
    --set storage.size=$DISK_SIZE

Note the last two lines of the command: ``--set storage.size=$DISK_SIZE`` and ``--set storage.storageClassName=$PV_CLASS_NAME``.
These lines associate the API server with the persistent volume created in :ref:`api-server-ha-create-pv`,
allowing the API server to use the persistent volume to store its state.

.. _api-server-ha-simulate-failure:

Simulate a catastrophic failure of the GKE cluster
--------------------------------------------------

To simulate a catastrophic failure of the GKE cluster, delete the GKE cluster.

Then, create a new GKE cluster and repeat the following sections:

- :ref:`api-server-ha-create-pv`
- :ref:`api-server-ha-deploy-api-server`

The new API server URL is different from the previous URL, so run ``sky api login`` again with the new server URL.

Once the new API server is up and running, it should retain the same state as the previous API server!

.. _api-server-ha-cleanup:

Cleanup
-------

Delete GKE cluster used for the exercise.

Delete the persistent disk on GCP.

.. code-block:: bash

    gcloud compute disks delete $DISK_NAME --zone=$ZONE

.. _api-server-ha-conclusion:

Conclusion
----------

This document demonstrates how a PersistentVolume can be used to persist the API server's state.

While this document uses a GKE cluster with a GCP persistent disk as a backing volume,
the same can be done with other cloud providers that provide a CSI provider to a persistent block storage device.

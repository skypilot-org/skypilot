.. _sky-api-server-deploy:

Deploying the SkyPilot API Server
==================================
The SkyPilot API server is packaged as a Helm chart which deploys a Kubernetes ingress controller and the API server.

.. tip::

    This guide is for admins to deploy the API server. If you are a user looking to connect to the API server, refer to  :ref:`sky-api-server-connect`.

Prerequisites
-------------

* A Kubernetes cluster with ports 30050 and 30051 available for NodePort services
* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/>`_

.. tip::

    If you do not have a Kubernetes cluster, refer to :ref:`Kubernetes Deployment Guides <kubernetes-deployment>` to set one up.

Step 1: Create a namespace for the API server
---------------------------------------------

Create a namespace in your Kubernetes cluster. This namespace will be used to deploy the API server pods, services and secrets.

.. code-block:: console

    $ NAMESPACE=skypilot
    $ kubectl create namespace $NAMESPACE

Step 2: Configure cloud accounts
--------------------------------

Following tabs describe how to configure credentials for different clouds on the API server. All cloud credentials are stored in Kubernetes secrets.


.. tab-set::

    .. tab-item:: Kubernetes
        :sync: kubernetes-creds-tab

        No action is required - SkyPilot will automatically use the same Kubernetes cluster as the API server.

        Support for other Kubernetes clusters is coming soon.
    
    .. tab-item:: AWS
        :sync: aws-creds-tab

        Make sure you have the access key id and secret access key.

        Create a Kubernetes secret with your AWS credentials:

        .. code-block:: bash

            NAMESPACE=skypilot
            kubectl create secret generic aws-credentials \
            -n $NAMESPACE \
            --from-literal=aws_access_key_id=YOUR_ACCESS_KEY_ID \
            --from-literal=aws_secret_access_key=YOUR_SECRET_ACCESS_KEY

        Replace ``YOUR_ACCESS_KEY_ID`` and ``YOUR_SECRET_ACCESS_KEY`` with your actual AWS credentials.

        When installing or upgrading the Helm chart, enable AWS credentials by setting ``awsCredentials.enabled=true``.

        .. code-block:: bash

            helm upgrade --install skypilot-platform skypilot/skypilot-platform --set awsCredentials.enabled=true
    
    .. tab-item:: GCP
        :sync: gcp-creds-tab

        Coming soon.


Step 3: Deploy the API Server Helm Chart
----------------------------------------

Install the SkyPilot Helm chart with the following command. 

.. code-block:: console

    $ helm repo add skypilot https://helm.skypilot.co
    $ NAMESPACE=skypilot
    $ WEB_USERNAME=skypilot
    $ WEB_PASSWORD=yourpassword
    $ AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
    $ helm upgrade --install skypilot-platform skypilot/skypilot-platform \
    --namespace $NAMESPACE \
    --set ingress.auth=$AUTH_STRING

To install a specific version, pass the ``--version`` flag to the ``helm upgrade`` command (e.g., ``--version 0.1.0``).

If you are using AWS credentials configured in the previous step, you can enable them by adding ``--set awsCredentials.enabled=true`` to the command.

.. tip::

    You can configure the password for the API server with the ``WEB_PASSWORD`` variable.

Step 4: Get the API server URL
------------------------------

Once the API server is deployed, we can fetch the API server URL.

We use nginx ingress to expose the API server. We also setup an additional NodePort service to expose the ingress controller.

.. note::

    Using NodePort is required because many cloud load balancers (e.g., GKE) do not work with websocket connections, which are required for our Kubernetes SSH tunneling. 

    If you do not need SSH tunneling, you can use a :ref:`LoadBalancer service <sky-api-server-loadbalancer>` to expose the API server.

1. Make sure ports 30050 and 30051 are open on your nodes.

2. Fetch the ingress controller URL with:

.. code-block:: console

    $ NODE_PORT=$(kubectl get svc nginx-ingress-controller-np -n skypilot -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}')
    $ NODE_IP=$(kubectl get nodes -o jsonpath='{ $.items[0].status.addresses[?(@.type=="ExternalIP")].address }')
    $ ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${NODE_IP}:${NODE_PORT}
    $ echo $ENDPOINT
    http://skypilot:yourpassword@1.1.1.1:30050


.. tip::
    
    You can customize the node ports with ``--set ingress.httpNodePort=<port> --set ingress.httpsNodePort=<port>`` to the helm upgrade command. 
    
    If set to null, Kubernetes will assign random ports in the NodePort range (default 30000-32767). Make sure to open these ports on your nodes.


.. _sky-api-server-loadbalancer:

.. tip::

    If you cannot use NodePort services, you may use the ingress LoadBalancer service directly exposed by the nginx Helm chart using ``--set ingress.httpNodePort=null --set ingress.httpsNodePort=null --set ingress-nginx.controller.service.type=LoadBalancer``.
    
    Then you can fetch the ingress controller URL with:

    .. code-block:: console

        $ ENDPOINT=$(kubectl get svc skypilot-platform-ingress-nginx-controller -n skypilot -o jsonpath='http://{.status.loadBalancer.ingress[0].ip}')
        $ echo $ENDPOINT
        http://1.1.1.1

    You will not be able to use SSH to access SkyPilot clusters on Kubernetes when using LoadBalancer services.


Test the API server by curling the health endpoint:

.. code-block:: console

    $ curl ${ENDPOINT}/api/health
    {"status":"healthy","api_version":"1","commit":"ba7542c6dcd08484d83145d3e63ec9966d5909f3-dirty","version":"1.0.0-dev0"}

If all looks good, you can now start using the API server. Refer to :ref:`sky-api-server-connect` to connect your local SkyPilot client to the API server.

Updating the API server
-----------------------

To update the API server, update your repositories with ``helm repo update`` and run the same ``helm upgrade`` command as in the installation step.

Uninstall
---------

To uninstall the API server, run:

.. code-block:: console

    $ helm uninstall skypilot-platform -n skypilot

This will delete the API server and all associated resources.

Other Notes
-----------

Fault Tolerance and State Persistence
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The skypilot API server is designed to be fault tolerant. If the API server pod is terminated, the Kubernetes will automatically create a new pod to replace it. 

To retain state during pod termination, we use a persistent volume claim. The persistent volume claim is backed by a PersistentVolume that is created by the Helm chart.

You can customize the storage settings using the following values by creating a ``values.yaml`` file:

.. code-block:: yaml

    storage:
      # Enable/disable persistent storage
      enabled: true
      # Storage class name - leave empty to use cluster default
      storageClassName: ""
      # Access modes - ReadWriteOnce or ReadWriteMany depending on storage class support
      accessMode: ReadWriteOnce
      # Storage size
      size: 10Gi
      # Optional selector for matching specific PVs
      selector: {}
        # matchLabels:
        #   environment: prod
      # Optional volume name for binding to specific PV
      volumeName: ""
      # Optional annotations
      annotations: {}

For example, to use a specific storage class and increase the storage size:

.. code-block:: yaml

    # values.yaml
    storage:
      enabled: true
      storageClassName: "standard"
      size: 20Gi

Apply the configuration using:

.. code-block:: console

    $ helm upgrade --install skypilot-platform skypilot/skypilot-platform -f values.yaml


Additional setup for EKS
^^^^^^^^^^^^^^^^^^^^^^^^

To support persistent storage for the API server's state, we need a storage class that supports persistent volumes. If you already have a storage class that supports persistent volumes, you can skip the following steps.

In this example, we will use the `Amazon EBS CSI driver <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_ to create a storage class that supports persistent volumes backed by Amazon EBS. You can also use other storage classes that support persistent volumes, such as `EFS <https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html>`_.

The steps below are based on the `official documentation <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_. Please follow the official documentation to adapt the steps to your cluster.

1. Make sure OIDC is enabled for your cluster. Follow the steps `here <https://docs.aws.amazon.com/eks/latest/userguide/enable-iam-roles-for-service-accounts.html>`_.

   a. You will need to create and bind an IAM role which has permissions to create EBS volumes. See `instructions here <https://docs.aws.amazon.com/eks/latest/userguide/associate-service-account-role.html>`_.

2. Install the `Amazon EBS CSI driver <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_. The recommended method is through creating an EKS add-on.

Once the EBS CSI driver is installed, the default ``gp2`` storage class will be backed by EBS volumes.


Setting Config YAML or Admin Policy
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The Helm chart supports setting the global SkyPilot config YAML and installing an admin policy before the API server starts. 

To do so, set ``apiService.preDeployHook`` to the commands you want to run. For example, to set the global config YAML and install an admin policy, create a ``values.yaml`` file with the following:

.. code-block:: yaml

    # values.yaml
    apiService:
      preDeployHook: |
       echo "Installing admin policy"
       pip install git+https://github.com/michaelvll/admin-policy-examples
       
       echo "Setting global SkyPilot config"
       mkdir -p ~/.sky
       cat <<EOF > ~/.sky/config.yaml
       admin_policy: admin_policy_examples.AddLabelsPolicy

       jobs:
       controller:
          resources:
             cpus: 2+
       
       allowed_clouds:
       - aws
       - kubernetes
       EOF

Then apply the values.yaml file using the `-f` flag when running the helm upgrade command:

.. code-block:: console

    $ helm upgrade --install skypilot-platform skypilot/skypilot-platform -f values.yaml

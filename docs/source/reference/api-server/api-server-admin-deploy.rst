.. _sky-api-server-deploy:

Deploying SkyPilot API Server
==============================

The SkyPilot API server is packaged as a Helm chart which deploys a Kubernetes ingress controller and the API server.

.. tip::

    This guide is for admins to deploy the API server. If you are a user looking to connect to the API server, refer to  :ref:`sky-api-server-connect`.

Prerequisites
-------------

* A Kubernetes cluster with LoadBalancer or NodePort service support
* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/>`_

.. tip::

    If you do not have a Kubernetes cluster, refer to :ref:`Kubernetes Deployment Guides <kubernetes-deployment>` to set one up.

    You can also deploy the API server on cloud VMs using an existing SkyPilot installation. See :ref:`sky-api-server-cloud-deploy`.

Step 1: Create a namespace and add Helm repository
--------------------------------------------------

The API server will be deployed in a namespace of your choice. You can either create the namespace manually:

.. code-block:: bash

    NAMESPACE=skypilot
    kubectl create namespace $NAMESPACE

Or let Helm create it automatically by adding the ``--create-namespace`` flag to the helm install command in Step 3.

Next, add the SkyPilot Helm repository:

.. code-block:: bash

    helm repo add skypilot https://helm.skypilot.co
    helm repo update

Step 2: Configure cloud accounts
--------------------------------

Following tabs describe how to configure credentials for different clouds on the API server. All cloud credentials are stored in Kubernetes secrets.


.. tab-set::

    .. tab-item:: Kubernetes
        :sync: kubernetes-creds-tab

        By default, SkyPilot will automatically use the same Kubernetes cluster as the API server:

        * To disable this behavior, set ``kubernetesCredentials.useApiServerCluster=false`` in the Helm chart values.
        * When running in the same cluster, tasks are launched in the same namespace as the API server. To use a different namespace for tasks, set ``kubernetesCredentials.inclusterNamespace=<namespace>`` when deploying the API server.

        To use a kubeconfig file to authenticate to other clusters, first create a Kubernetes secret with the kubeconfig file:

        .. code-block:: bash

            NAMESPACE=skypilot
            kubectl create secret generic kube-credentials \
              -n $NAMESPACE \
              --from-file=config=~/.kube/config


        Once the secret is created, set ``kubernetesCredentials.useKubeconfig=true`` and ``kubernetesCredentials.kubeconfigSecretName`` in the Helm chart values to use the kubeconfig file for authentication:

        .. code-block:: bash

            helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
              --set kubernetesCredentials.useKubeconfig=true \
              --set kubernetesCredentials.kubeconfigSecretName=kube-credentials \
              --set kubernetesCredentials.useApiServerCluster=true


        .. tip::

            If you are using a kubeconfig file that contains `exec-based authentication <https://kubernetes.io/docs/reference/access-authn-authz/authentication/#configuration>`_ (e.g., GKE's default ``gke-gcloud-auth-plugin`` based authentication), you will need to strip the path information from the ``command`` field in the exec configuration.
            You can use the ``exec_kubeconfig_converter.py`` script to do this.

            .. code-block:: bash

                python -m sky.utils.kubernetes.exec_kubeconfig_converter --input ~/.kube/config --output ~/.kube/config.converted

            Then create the Kubernetes secret with the converted kubeconfig file ``~/.kube/config.converted``.

        .. tip::

            To use multiple Kubernetes clusters from the config file, you will need to add the context names to ``allowed_contexts`` in the SkyPilot config file. See :ref:`sky-api-server-config` on how to set the config file.

            You can also set both ``useKubeconfig`` and ``useApiServerCluster`` at the same time to configure the API server to use an external Kubernetes cluster in addition to the API server's own cluster.


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

            helm upgrade --install skypilot skypilot/skypilot-nightly --devel --set awsCredentials.enabled=true

    .. tab-item:: GCP
        :sync: gcp-creds-tab

        We use service accounts to authenticate with GCP. Refer to :ref:`GCP service account <gcp-service-account>` guide on how to set up a service account.

        Once you have the JSON key for your service account, create a Kubernetes secret to store it:

        .. code-block:: bash

            NAMESPACE=skypilot
            kubectl create secret generic gcp-credentials \
              -n $NAMESPACE \
              --from-file=gcp-cred.json=YOUR_SERVICE_ACCOUNT_JSON_KEY.json

        When installing or upgrading the Helm chart, enable GCP credentials by setting ``gcpCredentials.enabled=true`` and ``gcpCredentials.projectId`` to your project ID:

        .. code-block:: bash

            helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
              --set gcpCredentials.enabled=true \
              --set gcpCredentials.projectId=YOUR_PROJECT_ID

        Replace ``YOUR_PROJECT_ID`` with your actual GCP project ID.

    .. tab-item:: Other clouds
        :sync: other-clouds-tab

        You can manually configure the credentials for other clouds by `kubectl exec` into the API server pod after it is deployed and running the relevant :ref:`installation commands<installation>`.

        Note that manually configured credentials will not be persisted across API server restarts.

        Support for configuring other clouds through secrets is coming soon!


Step 3: Deploy the API server Helm chart
----------------------------------------

Install the SkyPilot Helm chart with the following command:

..
   Note that helm requires --devel flag to use any version marked with pre-release flags (e.g., 1.0.0-dev.YYYYMMDD in our versioning).
   TODO: We should add a tab for stable release and a tab for nightly release once we have a stable release with API server.

.. code-block:: bash

    # The following variables will be used throughout the guide
    NAMESPACE=skypilot
    RELEASE_NAME=skypilot
    WEB_USERNAME=skypilot
    WEB_PASSWORD=yourpassword
    AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --create-namespace \
      --set ingress.authCredentials=$AUTH_STRING

The ``--namespace`` flag specifies which namespace to deploy the API server in, and ``--create-namespace`` will create the namespace if it doesn't exist.

To install a specific version, pass the ``--version`` flag to the ``helm upgrade`` command (e.g., ``--version 0.1.0``).

If you configured any cloud credentials in the previous step, make sure to enable them by adding the relevant flags (e.g., ``--set awsCredentials.enabled=true``) to the command.

.. tip::

    You can configure the password for the API server with the ``WEB_PASSWORD`` variable.

.. tip::

    If you already have a Kubernetes secret containing basic auth credentials, you can use it directly by setting ``ingress.authSecret`` instead of ``ingress.authCredentials``:

    .. code-block:: bash

        helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
          --namespace $NAMESPACE \
          --create-namespace \
          --set ingress.authSecret=my-existing-auth-secret

    The secret must be in the same namespace as the API server and must contain a key named ``auth`` with the basic auth credentials in htpasswd format.

.. _sky-get-api-server-url:

Step 4: Get the API server URL
------------------------------

Once the API server is deployed, we can fetch the API server URL. We use nginx ingress to expose the API server.

Our default of using a NodePort service is the recommended way to expose the API server because some cloud load balancers (e.g., GKE) do not work with websocket connections, which are required for our Kubernetes SSH tunneling.

.. tab-set::

    .. tab-item:: LoadBalancer (Default)
        :sync: loadbalancer-tab

        Fetch the ingress controller URL:

        .. code-block:: console

            $ HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
            $ ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${HOST}
            $ echo $ENDPOINT
            http://skypilot:yourpassword@1.1.1.1
        
        .. tip::
            
            If you're using a Kubernetes cluster without LoadBalancer support, you may get an empty IP address in the output above.
            In that case, use the NodePort option instead.
        
        .. tip::

            For fine-grained control over the LoadBalancer service, refer to the `helm values of ingress-nginx <https://artifacthub.io/packages/helm/ingress-nginx/ingress-nginx#values>`_. Note that all values should be put under ``ingress-nginx.`` prefix since the ingress-nginx chart is installed as a subchart.

    .. tab-item:: NodePort
        :sync: nodeport-tab

        1. Select two ports on your nodes that are not in use and allow network inbound traffic to them. 30050 and 30051 will be used in this example.

        2. Upgrade the API server to use NodePort, and set the node ports to the selected ports:

        .. code-block:: bash

            $ helm upgrade -n $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --set ingress-nginx.controller.service.type=NodePort \
              --set ingress-nginx.controller.service.nodePorts.http=30050 \
              --set ingress-nginx.controller.service.nodePorts.https=30051

        3. Fetch the ingress controller URL with:

        .. code-block:: console

            $ NODE_PORT=$(kubectl get svc ${RELEASE_NAME}-ingress-controller-np -n $NAMESPACE -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}')
            $ NODE_IP=$(kubectl get nodes -o jsonpath='{ $.items[0].status.addresses[?(@.type=="ExternalIP")].address }')
            $ ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${NODE_IP}:${NODE_PORT}
            $ echo $ENDPOINT
            http://skypilot:yourpassword@1.1.1.1:30050

        .. tip::
            
            You can also omit ``ingress-nginx.controller.service.nodePorts.http`` and ``ingress-nginx.controller.service.nodePorts.https`` to use random ports in the NodePort range (default 30000-32767). Make sure these ports are open on your nodes if you do so.

        .. tip::

            To avoid frequent IP address changes on nodes by your cloud provider, you can attach a static IP address to your nodes (`instructions for GKE <https://cloud.google.com/compute/docs/ip-addresses/configure-static-external-ip-address>`_) and use it as the ``NODE_IP`` in the command above.


Step 5: Test the API server
---------------------------

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

.. code-block:: bash

    helm uninstall skypilot -n skypilot

This will delete the API server and all associated resources.

Other notes
-----------

Fault tolerance and state persistence
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

.. code-block:: bash

    helm upgrade --install skypilot skypilot/skypilot-nightly --devel -f values.yaml


Additional setup for EKS
^^^^^^^^^^^^^^^^^^^^^^^^

To support persistent storage for the API server's state, we need a storage class that supports persistent volumes. If you already have a storage class that supports persistent volumes, you can skip the following steps.

We will use the `Amazon EBS CSI driver <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_ to create a storage class that supports persistent volumes backed by Amazon EBS. You can also use other storage classes that support persistent volumes, such as `EFS <https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html>`_.

The steps below are based on the `official documentation <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_. Please follow the official documentation to adapt the steps to your cluster.

1. Make sure OIDC is enabled for your cluster. Follow the steps `here <https://docs.aws.amazon.com/eks/latest/userguide/enable-iam-roles-for-service-accounts.html>`_.

   a. You will need to create and bind an IAM role which has permissions to create EBS volumes. See `instructions here <https://docs.aws.amazon.com/eks/latest/userguide/associate-service-account-role.html>`_.

2. Install the `Amazon EBS CSI driver <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_. The recommended method is through creating an EKS add-on.

Once the EBS CSI driver is installed, the default ``gp2`` storage class will be backed by EBS volumes.

.. _sky-api-server-config:

Setting the SkyPilot config
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The Helm chart supports setting the global SkyPilot config YAML file on the API server. The config file is mounted as ``~/.sky/config.yaml`` in the API server container.

To set the config file, pass ``--set-file apiService.config=path/to/your/config.yaml`` to the ``helm`` command:

.. code-block:: bash

    # Create the config.yaml file
    cat <<EOF > config.yaml
    admin_policy: admin_policy_examples.AddLabelsPolicy

    jobs:
      controller:
        resources:
            cpus: 2+

    allowed_clouds:
      - aws
      - kubernetes

    kubernetes:
      allowed_contexts:
        - my-context
        - my-other-context
    EOF

    # Install the API server with the config file
    helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
      --set-file apiService.config=config.yaml

You can also directly set config values in the ``values.yaml`` file.

Setting an admin policy
^^^^^^^^^^^^^^^^^^^^^^^

The Helm chart supports installing an admin policy before the API server starts.

To do so, set ``apiService.preDeployHook`` to the commands you want to run. For example, to install an admin policy, create a ``values.yaml`` file with the following:

.. code-block:: yaml

    # values.yaml
    apiService:
      preDeployHook: |
       echo "Installing admin policy"
       pip install git+https://github.com/michaelvll/admin-policy-examples

      config: |
        admin_policy: admin_policy_examples.AddLabelsPolicy

Then apply the values.yaml file using the `-f` flag when running the helm upgrade command:

.. code-block:: bash

    helm upgrade --install skypilot skypilot/skypilot-nightly --devel -f values.yaml

.. _sky-migrate-legacy-service:

Migrate from legacy NodePort service
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are upgrading from an early 0.8.0 nightly with a previously deployed NodePort service (named ``${RELEASE_NAME}-ingress-controller-np``), an error will be raised to ask for migration. In addition, a new service will be created to expose the API server (using ``LoadBalancer`` service type by default). You can choose any of the following options to proceed the upgrade process based on your needs:

- Keep the legacy NodePort service and gradually migrate to the new LoadBalancer service:

  Add ``--set ingress.nodePortEnabled=true`` to your ``helm upgrade`` command to keep the legacy NodePort service. Existing clients can continue to use the previous NodePort service. After all clients have been migrated to the new service, you can disable the legacy NodePort service by adding ``--set ingress.nodePortEnabled=false`` to the ``helm upgrade`` command.
  
- Disable the legacy NodePort service:

  Add ``--set ingress.nodePortEnabled=false`` to your ``helm upgrade`` command to disable the legacy NodePort service. Clients will need to use the new service to connect to the API server.

.. note::

    Make sure there is no clients using the NodePort service before disabling it.

.. note::

    Refer to :ref:`sky-get-api-server-url` for how to customize and/or connect to the new service.

.. _sky-api-server-cloud-deploy:

Alternative: Deploy on cloud VMs
--------------------------------

You can also deploy the API server directly on cloud VMs using an existing SkyPilot installation.

Step 1: Use SkyPilot to deploy the API server on a cloud VM
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Write the SkyPilot API server YAML file and use ``sky launch`` to deploy the API server:

.. Do not use ``console`` here because that will break the indentation of the YAML file during copy paste.

.. code-block:: bash

    # Write the YAML to a file
    cat <<EOF > skypilot-api-server.yaml
    resources:
      cpus: 8+
      memory: 16+
      ports: 46580
      image_id: docker:berkeleyskypilot/skypilot-nightly:latest

    run: |
      sky api start --deploy
    EOF

    # Deploy the API server
    sky launch -c api-server skypilot-api-server.yaml

Step 2: Get the API server URL
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Once the API server is deployed, you can fetch the API server URL with:

.. code-block:: console

    $ sky status --endpoint 46580 api-server
    http://a.b.c.d:46580


Test the API server by curling the health endpoint:

.. code-block:: console

    $ curl ${ENDPOINT}/health
    SkyPilot API Server: Healthy

If all looks good, you can now start using the API server. Refer to :ref:`sky-api-server-connect` to connect your local SkyPilot client to the API server.

.. note::

    API server deployment using the above YAML does not have any authentication by default. We recommend adding a authentication layer (e.g., nginx reverse proxy) or using the :ref:`SkyPilot helm chart <sky-api-server-deploy>` on a Kubernetes cluster for a more secure deployment.

.. tip::

    If you are installing SkyPilot API client in the same environment, we recommend using a different python environment (venv, conda, etc.) to avoid conflicts with the SkyPilot installation used to deploy the API server.

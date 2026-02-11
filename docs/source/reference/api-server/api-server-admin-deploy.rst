.. _sky-api-server-deploy:

Deploying SkyPilot API Server
==============================

The SkyPilot API server is packaged as a Helm chart which deploys a Kubernetes ingress controller and the API server.

.. tip::

    This guide is for admins to deploy the API server. If you are a user looking to connect to the API server, refer to  :ref:`sky-api-server-connect`.

.. tip::

    Deploying the API server to a Kubernetes cluster using Helm provides the best reliability and enables additional features such as :ref:`OAuth2 authentication <api-server-oauth>` and :ref:`metrics <api-server-metrics-setup>`. However, there are two alternative options available for special cases:

    * :ref:`Deploying the API server on cloud VMs <sky-api-server-cloud-deploy>` if you do not have a Kubernetes cluster and do not need the additional features.
    * :ref:`Sharing an API server for multiple users on a single machine <sky-api-server-in-docker>` if you want to share the API server with multiple users on a single machine instead of exposing it to the public internet (e.g. a shared bastion machine).

Prerequisites
-------------

* A Kubernetes cluster with LoadBalancer or NodePort service support
* `Helm <https://helm.sh/docs/intro/install/>`_
* `kubectl <https://kubernetes.io/docs/tasks/tools/>`_

.. tip::

    If you do not have a Kubernetes cluster, refer to :ref:`Kubernetes Deployment Guides <kubernetes-deployment>` to set one up.

.. _sky-api-server-helm-deploy-command:

Step 1: Deploy the API server Helm chart
----------------------------------------

Install the SkyPilot Helm chart with the following command:

..
   Note that helm requires --devel flag to use any version marked with pre-release flags (e.g., 1.0.0-dev.YYYYMMDD in our versioning).
   TODO: We should add a tab for stable release and a tab for nightly release once we have a stable release with API server.

.. code-block:: bash

    # Ensure the helm repository is added and up to date
    helm repo add skypilot https://helm.skypilot.co
    helm repo update

    # The following variables will be used throughout the guide
    # NAMESPACE is the namespace to deploy the API server in
    NAMESPACE=skypilot
    # RELEASE_NAME is the name of the helm release, must be unique within the namespace
    RELEASE_NAME=skypilot
    # Set up basic username/password HTTP auth, or use OAuth2 proxy
    WEB_USERNAME=skypilot
    WEB_PASSWORD=yourpassword
    AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
    # Deploy the API server
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --create-namespace \
      --set ingress.authCredentials=$AUTH_STRING

.. dropdown:: Flags explanation

    Here is a breakdown of the flags used in the command above:

    * ``upgrade --install``: Upgrade the API server if it already exists, or install it if it doesn't exist.
    * ``--devel``: Use the latest development version of the SkyPilot helm chart. To use a specific version, pass the ``--version`` flag to the ``helm upgrade`` command (e.g., ``--version 0.1.0``).
    * ``--namespace $NAMESPACE``: Specify the namespace to deploy the API server in.
    * ``--create-namespace``: Create the namespace if it doesn't exist.
    * :ref:`--set ingress.authCredentials=$AUTH_STRING <helm-values-ingress-authCredentials>`: Set the basic auth credentials for the API server.

    For more details on the available configuration options, refer to :ref:`SkyPilot API Server Helm Chart Values <helm-values-spec>`.

The above command will install a SkyPilot API server and ingress-nginx controller in the given namespace, which by default conflicts with other installations. To deploy multiple API servers, refer to :ref:`Reusing ingress-nginx controller for API server <sky-api-server-helm-multiple-deploy>`. To use a different ingress controller, refer to :ref:`sky-api-server-custom-ingress`

.. tip::

    The API server deployed will be configured to use the hosting Kubernetes cluster to launch tasks by default. Refer to :ref:`sky-api-server-configure-credentials` to configure credentials for more clouds and Kubernetes clusters.

After the API server is deployed, you can inspect the API server pod status with:

.. code-block:: bash

    kubectl get pods --namespace $NAMESPACE -l app=${RELEASE_NAME}-api --watch

You should see the pod is initializing and finally becomes running and ready. If not, refer to :ref:`sky-api-server-troubleshooting-helm` to diagnose the issue.

The API server above is deployed with a basic auth provided by Nginx. To use advanced OAuth2 authentication, refer to :ref:`Using OAuth for API server <api-server-oauth>`.

.. _sky-get-api-server-url:

Step 2: Get the API server URL
------------------------------

Once the API server is deployed, we can fetch the API server URL. The chart uses nginx ingress to expose the API server and exposes the ingress to internet using a LoadBalancer service by default. If you are using a Kubernetes cluster without LoadBalancer support, you can use the NodePort option below instead.

.. tab-set::

    .. tab-item:: LoadBalancer (Default)
        :sync: loadbalancer-tab

        Fetch the ingress controller URL:

        .. code-block:: console

            $ HOST=$(kubectl get svc ${RELEASE_NAME}-ingress-nginx-controller --namespace $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
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

            $ helm upgrade --namespace $NAMESPACE $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --reuse-values \
              --set ingress-nginx.controller.service.type=NodePort \
              --set ingress-nginx.controller.service.nodePorts.http=30050 \
              --set ingress-nginx.controller.service.nodePorts.https=30051

        3. Fetch the ingress controller URL with:

        .. code-block:: console

            $ NODE_PORT=$(kubectl get svc ${RELEASE_NAME}-ingress-controller-np --namespace $NAMESPACE -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}')
            $ NODE_IP=$(kubectl get nodes -o jsonpath='{ $.items[0].status.addresses[?(@.type=="ExternalIP")].address }')
            $ HOST=${NODE_IP}:${NODE_PORT}
            $ ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${HOST}
            $ echo $ENDPOINT
            http://skypilot:yourpassword@1.1.1.1:30050

        .. tip::

            You can also omit ``ingress-nginx.controller.service.nodePorts.http`` and ``ingress-nginx.controller.service.nodePorts.https`` to use random ports in the NodePort range (default 30000-32767). Make sure these ports are open on your nodes if you do so.

        .. tip::

            To avoid frequent IP address changes on nodes by your cloud provider, you can attach a static IP address to your nodes (`instructions for GKE <https://cloud.google.com/compute/docs/ip-addresses/configure-static-external-ip-address>`_) and use it as the ``NODE_IP`` in the command above.


Step 3: Test the API server
---------------------------

Test the API server by curling the health endpoint:

.. code-block:: console

    $ curl ${ENDPOINT}/api/health
    {"status":"healthy","api_version":"1","commit":"ba7542c6dcd08484d83145d3e63ec9966d5909f3-dirty","version":"1.0.0-dev0"}

If all looks good, you can now start using the API server. Refer to :ref:`sky-api-server-connect` to connect your local SkyPilot client to the API server.

.. _sky-api-server-configure-credentials:

Optional: Configure cloud accounts
----------------------------------

Following tabs describe how to configure credentials for different clouds on the API server. All cloud credentials are stored in Kubernetes secrets.

.. note::

   If you don't have SkyPilot API server deployed yet, please refer to :ref:`sky-api-server-helm-deploy-command`, for the additional values you might want to set during the helm deployment below.

    When you configure credentials after the API server is deployed, an API server restart will be automatically triggered to apply the new credentials. Refer to :ref:`sky-api-server-upgrade` for more details about the potential downtime and mitigation.

.. tab-set::

    .. tab-item:: Kubernetes
        :sync: kubernetes-creds-tab

        By default, SkyPilot API server is granted permissions to use its hosting Kubernetes cluster and will launch tasks in the same namespace as the API server:

        * To disable using the hosting Kubernetes cluster, set ``kubernetesCredentials.useApiServerCluster=false`` in the Helm chart values.
        * To use a different namespace for tasks, set ``kubernetesCredentials.inclusterNamespace=<namespace>`` in the Helm chart values.

        .. tip::

            The default permissions granted to the API server works out of box. For further hardening, you can refer to :ref:`Setting minimum permissions in helm deployment <minimum-permissions-in-helm>` to understand the permissions and how to customize them.

        To authenticate to other clusters, first create a Kubernetes secret with the kubeconfig file with :ref:`necessary permissions <cloud-permissions-kubernetes>`:

        .. code-block:: bash

            kubectl create secret generic kube-credentials \
              --namespace $NAMESPACE \
              --from-file=config=$HOME/.kube/config


        Once the secret is created, set ``kubernetesCredentials.useKubeconfig=true`` and ``kubernetesCredentials.kubeconfigSecretName`` in the Helm chart values to use the kubeconfig for authentication:

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set kubernetesCredentials.useKubeconfig=true \
              --set kubernetesCredentials.kubeconfigSecretName=kube-credentials

        .. tip::

            If you are using a kubeconfig file that contains `exec-based authentication <https://kubernetes.io/docs/reference/access-authn-authz/authentication/#configuration>`_ (e.g., GKE's default ``gke-gcloud-auth-plugin``, Nebius Managed Kubernetes, OCI, etc.), you will need to generate a kubeconfig with static authentication instead.
            You can use the ``generate_kubeconfig.sh`` script to do this.

            .. code-block:: bash

                # Download the script
                curl -O https://raw.githubusercontent.com/skypilot-org/skypilot/refs/heads/master/sky/utils/kubernetes/generate_kubeconfig.sh && chmod +x generate_kubeconfig.sh

                # Generate the kubeconfig
                export KUBECONFIG=$HOME/.kube/config # or the path to your kubeconfig file
                ./generate_kubeconfig.sh

            Then create the Kubernetes secret with the generated kubeconfig file ``./kubeconfig``.


        .. dropdown:: Update Kubernetes credentials

           After Kubernetes credentials are enabled, you can update the kubeconfig file in ``kube-credentials`` by:

           1. Replace the existing secret in place:

              .. code-block:: bash

                  kubectl delete secret kube-credentials
                  kubectl create secret generic kube-credentials \
                    --namespace $NAMESPACE \
                    --from-file=config=$HOME/.kube/config

           2. Then it will take tens of seconds to take effect on the API server. You can verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  # If `SSH Node Pools` is not enabled
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.kube/config
                  # If `SSH Node Pools` is enabled
                  #kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /var/skypilot/kubeconfig/config

        To use multiple Kubernetes clusters, you will need to add the context names to ``allowed_contexts`` in the SkyPilot config. An example config file that allows using the hosting Kubernetes cluster and two additional Kubernetes clusters is shown below:

        .. code-block:: yaml

            kubernetes:
              allowed_contexts:
              # The hosting Kubernetes cluster, you cannot set this if the hosting cluster is disabled by kubernetesCredentials.useApiServerCluster=false
              - in-cluster
              # The additional Kubernetes context names in the kubeconfig you configured
              - context1
              - context2

        Refer to :ref:`config-yaml-kubernetes-allowed-contexts` for how to set the SkyPilot config in Helm chart values.

    .. tab-item:: AWS
        :sync: aws-creds-tab

        We use static credentials to authenticate with AWS. Once you have the credentials, create a Kubernetes secret to store it.
        We support two different options for AWS credentials.

        **Option 1: Single profile (default)**

        Use this if you only need a single set of AWS credentials. Create a Kubernetes secret with your AWS access key and secret key:

        .. code-block:: bash

            kubectl create secret generic aws-credentials \
              --namespace $NAMESPACE \
              --from-literal=aws_access_key_id=YOUR_ACCESS_KEY_ID \
              --from-literal=aws_secret_access_key=YOUR_SECRET_ACCESS_KEY

        Replace ``YOUR_ACCESS_KEY_ID`` and ``YOUR_SECRET_ACCESS_KEY`` with your actual AWS credentials.

        Enable it by setting ``awsCredentials.enabled=true`` in the Helm values file.

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                --namespace $NAMESPACE \
                --reuse-values \
                --set awsCredentials.enabled=true

        .. dropdown:: Update AWS credentials (single profile)

           After AWS credentials are enabled, update the access or secret key in ``aws-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic aws-credentials-new \
                    --namespace $NAMESPACE \
                    --from-literal=aws_access_key_id=YOUR_ACCESS_KEY_ID \
                    --from-literal=aws_secret_access_key=YOUR_SECRET_ACCESS_KEY

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set awsCredentials.awsSecretName=aws-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret aws-credentials
                  kubectl create secret generic aws-credentials \
                    --namespace $NAMESPACE \
                    --from-literal=aws_access_key_id=YOUR_ACCESS_KEY_ID \
                    --from-literal=aws_secret_access_key=YOUR_SECRET_ACCESS_KEY

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.aws/credentials

        **Option 2: Multiple profiles (for multiple workspaces)**

        Use this if you need different AWS profiles for different workspaces. Create a Kubernetes secret from your AWS credentials file:

        .. code-block:: bash

            kubectl create secret generic aws-credentials \
              --namespace $NAMESPACE \
              --from-file=credentials=$HOME/.aws/credentials

        Enable it by setting ``awsCredentials.enabled=true`` and ``awsCredentials.useCredentialsFile=true`` in the Helm values file.

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                --namespace $NAMESPACE \
                --reuse-values \
                --set awsCredentials.enabled=true \
                --set awsCredentials.useCredentialsFile=true

        .. dropdown:: Update AWS credentials (multiple profiles)

           After AWS credentials are enabled, you can update the credentials file in ``aws-credentials`` by:

           1. Replace the existing secret in place:

              .. code-block:: bash

                  kubectl delete secret aws-credentials
                  kubectl create secret generic aws-credentials \
                    --namespace $NAMESPACE \
                    --from-file=credentials=$HOME/.aws/credentials

           2. Then it will take tens of seconds to take effect on the API server. You can verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.aws/credentials

        .. dropdown:: Use existing AWS credentials

            You can also set the following values to use a secret that already contains your AWS credentials:

            .. code-block:: bash

                # TODO: replace with your secret name and keys in the secret
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set awsCredentials.enabled=true \
                    --set awsCredentials.awsSecretName=your_secret_name \
                    --set awsCredentials.accessKeyIdKeyName=aws_access_key_id \
                    --set awsCredentials.secretAccessKeyKeyName=aws_secret_access_key

            Or if using credentials file:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set awsCredentials.enabled=true \
                    --set awsCredentials.useCredentialsFile=true \
                    --set awsCredentials.awsSecretName=your_secret_name

    .. tab-item:: GCP
        :sync: gcp-creds-tab

        We use service accounts to authenticate with GCP. Refer to :ref:`GCP service account <gcp-service-account>` guide on how to set up a service account.

        Once you have the JSON key for your service account, create a Kubernetes secret to store it:

        .. code-block:: bash

            kubectl create secret generic gcp-credentials \
              --namespace $NAMESPACE \
              --from-file=gcp-cred.json=YOUR_SERVICE_ACCOUNT_JSON_KEY.json

        When installing or upgrading the Helm chart, enable GCP credentials by setting ``gcpCredentials.enabled=true`` and ``gcpCredentials.projectId`` to your project ID:

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set gcpCredentials.enabled=true \
              --set gcpCredentials.projectId=YOUR_PROJECT_ID

        .. dropdown:: Use existing GCP credentials

            You can also set the following values to use a secret that already contains your GCP credentials:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set gcpCredentials.enabled=true \
                    --set gcpCredentials.gcpSecretName=your_secret_name

        .. dropdown:: Update GCP credentials

           After GCP credentials are enabled, you can update the credentials file in ``gcp-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic gcp-credentials-new \
                    --namespace $NAMESPACE \
                    --from-file=gcp-cred.json=YOUR_SERVICE_ACCOUNT_JSON_KEY_NEW.json

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set gcpCredentials.gcpSecretName=gcp-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret gcp-credentials
                  kubectl create secret generic gcp-credentials \
                    --namespace $NAMESPACE \
                    --from-file=gcp-cred.json=YOUR_SERVICE_ACCOUNT_JSON_KEY.json

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- ls -lart /root/.config/gcloud

    .. tab-item:: RunPod
        :sync: runpod-creds-tab

        SkyPilot API server use **API key** to authenticate with RunPod. To configure RunPod access, go to the `Settings <https://www.runpod.io/console/user/settings>`_ page on your RunPod console and generate an **API key**.

        Once the key is generated, create a Kubernetes secret to store it:

        .. code-block:: bash

            kubectl create secret generic runpod-credentials \
              --namespace $NAMESPACE \
              --from-literal api_key=YOUR_API_KEY

        When installing or upgrading the Helm chart, enable RunPod credentials by setting ``runpodCredentials.enabled=true``

        .. dropdown:: Use existing RunPod credentials

            You can also set the following values to use a secret that already contains your RunPod API key:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set runpodCredentials.enabled=true \
                    --set runpodCredentials.runpodSecretName=your_secret_name

        .. dropdown:: Update RunPod credentials

           After RunPod credentials are enabled, you can update the API key in ``runpod-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic runpod-credentials-new \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY_NEW

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set runpodCredentials.runpodSecretName=runpod-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret runpod-credentials
                  kubectl create secret generic runpod-credentials \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.runpod/config.toml

    .. tab-item:: Lambda
        :sync: lambda-creds-tab

        SkyPilot API server uses an **API key** to authenticate with Lambda. To configure Lambda access, go to the `API Keys <https://cloud.lambda.ai/api-keys/cloud-api>`_ page on your Lambda Cloud console and generate an **API key**.

        Once the key is generated, create a Kubernetes secret to store it:

        .. code-block:: bash

            kubectl create secret generic lambda-credentials \
              --namespace $NAMESPACE \
              --from-literal api_key=YOUR_API_KEY

        When installing or upgrading the Helm chart, enable Lambda credentials by setting ``lambdaCredentials.enabled=true``

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set lambdaCredentials.enabled=true

        .. dropdown:: Use existing Lambda credentials

            You can also set the following values to use a secret that already contains your Lambda credentials:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set lambdaCredentials.enabled=true \
                    --set lambdaCredentials.lambdaSecretName=your_secret_name

        .. dropdown:: Update Lambda credentials

           After Lambda credentials are enabled, you can update the API key in ``lambda-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic lambda-credentials-new \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY_NEW

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set lambdaCredentials.lambdaSecretName=lambda-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret lambda-credentials
                  kubectl create secret generic lambda-credentials \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.lambda_cloud/lambda_keys

    .. tab-item:: Nebius
        :sync: nebius-creds-tab

        We use service accounts to authenticate with Nebius. Refer to :ref:`Nebius service account <nebius-service-account>` guide on how to set up a service account.

        Once you have the JSON credentials for your service account, create a Kubernetes secret to store it:

        .. code-block:: bash

            kubectl create secret generic nebius-credentials \
              --namespace $NAMESPACE \
              --from-file=credentials.json=$HOME/.nebius/credentials.json

        Optionally, if you have multiple credentials files used in :ref:`workspaces <workspaces>`, you can create a secret with multiple files, following the pattern ``--from-file=<filename>=$HOME/.nebius/<filename>``. Files in this secret will be linked to `~/.nebius/` in the container.

        .. code-block:: bash

            kubectl create secret generic nebius-credentials \
              --namespace $NAMESPACE \
              --from-file=credentials.json=$HOME/.nebius/credentials.json \
              --from-file=serviceaccount-1-credentials.json=$HOME/.nebius/serviceaccount-1-credentials.json \
              --from-file=serviceaccount-2-credentials.json=$HOME/.nebius/serviceaccount-2-credentials.json


        .. code-block:: yaml

            # SkyPilot config

            workspaces:

              team-a:
                nebius:
                  credentials_file_path: ~/.nebius/serviceaccount-1-credentials.json
                  tenant_id: tenant-rrww0kh3nnfo7v0dgw

              team-b:
                nebius:
                  credentials_file_path: ~/.nebius/serviceaccount-2-credentials.json
                  tenant_id: tenant-52czfp5clbtq0er1ol

        When installing or upgrading the Helm chart, enable Nebius credentials by setting ``nebiusCredentials.enabled=true`` and ``nebiusCredentials.tenantId`` to your tenant ID:

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set nebiusCredentials.enabled=true \
              --set nebiusCredentials.tenantId=YOUR_TENANT_ID

        .. dropdown:: Use existing Nebius credentials

            You can also set the following values to use a secret that already contains your Nebius credentials:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set nebiusCredentials.enabled=true \
                    --set nebiusCredentials.nebiusSecretName=your_secret_name

        .. dropdown:: Update Nebius credentials

           After Nebius credentials are enabled, you can update the credentials file in ``nebius-credentials`` by:

           1. Replace the existing secret in place:

              .. code-block:: bash

                  kubectl delete secret nebius-credentials
                  kubectl create secret generic nebius-credentials \
                    --namespace $NAMESPACE \
                    --from-file=credentials=$HOME/.nebius/credentials.json

           2. Then it will take tens of seconds to take effect on the API server. You can verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.nebius/credentials.json

    .. tab-item:: Vast
        :sync: vast-creds-tab

        SkyPilot API server uses an **API key** to authenticate with Vast. To configure Vast access, go to the `Account <https://cloud.vast.ai/account/>`_ page on your Vast console and get your **API key**.

        Once the key is obtained, create a Kubernetes secret to store it:

        .. code-block:: bash

            kubectl create secret generic vast-credentials \
              --namespace $NAMESPACE \
              --from-literal api_key=YOUR_API_KEY

        When installing or upgrading the Helm chart, enable Vast credentials by setting ``vastCredentials.enabled=true``

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set vastCredentials.enabled=true

        .. dropdown:: Use existing Vast credentials

            You can also set the following values to use a secret that already contains your Vast credentials:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set vastCredentials.enabled=true \
                    --set vastCredentials.vastSecretName=your_secret_name

        .. dropdown:: Update Vast credentials

           After Vast credentials are enabled, you can update the API key in ``vast-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic vast-credentials-new \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY_NEW

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set vastCredentials.vastSecretName=vast-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret vast-credentials
                  kubectl create secret generic vast-credentials \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.config/vastai/vast_api_key

    .. tab-item:: SSH Node Pools
        :sync: ssh-node-pools-tab

        SkyPilot can configure a set of existing machines to be used as a :ref:`SSH Node Pool <existing-machines>`.

        To configure SSH node pools for the API server, create your SSH Node Pool :ref:`configuration file <defining-ssh-node-pools>` ``ssh_node_pools.yaml`` and set the :ref:`apiService.sshNodePools <helm-values-apiService-sshNodePools>` to the file path:

        .. code-block:: bash

            # RELEASE_NAME and NAMESPACE are the same as the ones used in the helm deployment
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set-file apiService.sshNodePools=/your/path/to/ssh_node_pools.yaml

        .. note::

           Updating the value of ``apiService.sshNodePools`` will not restart the API server but it will take tens of seconds to take effect on the API server.
           You can verify the config updates on the API server by running the following command:

           .. code-block:: bash

            # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
            API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
            kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.sky/ssh_node_pools.yaml

        If your ``ssh_node_pools.yaml`` requires SSH keys, create a secret that contains the keys and set the :ref:`apiService.sshKeySecret <helm-values-apiService-sshKeySecret>` to the secret name:

        .. code-block:: bash

            SECRET_NAME=apiserver-ssh-key

            # Create a secret that contains the SSH keys
            # The NAMESPACE should be consistent with the API server deployment
            kubectl create secret generic $SECRET_NAME \
              --namespace $NAMESPACE \
              --from-file=id_rsa=/path/to/id_rsa \
              --from-file=other_id_rsa=/path/to/other_id_rsa

            # Keys will be mounted to ~/.ssh/ (e.g., ~/.ssh/id_rsa, ~/.ssh/other_id_rsa)
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set apiService.sshKeySecret=$SECRET_NAME

        .. dropdown:: Update SSH key credentials

           After SSH key credentials are enabled, you can update the credentials file in ``$SECRET_NAME`` by:

           1. Replace the existing secret in place:

              .. code-block:: bash

                  kubectl delete secret $SECRET_NAME
                  kubectl create secret generic $SECRET_NAME \
                    --namespace $NAMESPACE \
                    --from-file=id_rsa=/path/to/id_rsa \
                    --from-file=other_id_rsa=/path/to/other_id_rsa

           2. Then it will take tens of seconds to take effect on the API server. You can verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- ls -lart /root/.ssh/

        After the API server is deployed, use the ``sky ssh up`` command to set up the SSH Node Pools. Refer to :ref:`existing-machines` for more details.

        .. note::

           SSH hosts configured on your local machine will not be available to the API server. It is recommended to set the SSH keys and password in the ``ssh_node_pools.yaml`` file for helm deployment.

    .. tab-item:: Cloudflare R2
        :sync: r2-creds-tab

        SkyPilot API server uses the same credentials as the :ref:`Cloudflare R2 installation <cloudflare-r2-installation>` to authenticate with Cloudflare R2.

        Once you have the credentials configured locally, you can store them in a Kubernetes secret:

        .. code-block:: bash

            kubectl create secret generic r2-credentials \
              --namespace $NAMESPACE \
              --from-file=r2.credentials=$HOME/.cloudflare/r2.credentials
              --from-file=accountid=$HOME/.cloudflare/accountid

        When installing or upgrading the Helm chart, enable Cloudflare R2 credentials by setting :ref:`r2Credentials.enabled <helm-values-r2credentials-enabled>` and :ref:`r2Credentials.r2SecretName <helm-values-r2credentials-r2secretname>`:

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set r2Credentials.enabled=true \
              --set r2Credentials.r2SecretName=r2-credentials

        .. dropdown:: Update Cloudflare R2 credentials

           After Cloudflare R2 credentials are enabled, you can update the credentials file in ``r2-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic r2-credentials-new \
                    --namespace $NAMESPACE \
                    --from-file=r2.credentials=$HOME/.cloudflare/r2.credentials
                    --from-file=accountid=$HOME/.cloudflare/accountid

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set r2Credentials.r2SecretName=r2-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret r2-credentials
                  kubectl create secret generic r2-credentials \
                    --namespace $NAMESPACE \
                    --from-file=r2.credentials=$HOME/.cloudflare/r2.credentials
                    --from-file=accountid=$HOME/.cloudflare/accountid

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.cloudflare/r2.credentials
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.cloudflare/accountid

    .. tab-item:: CoreWeave
        :sync: coreweave-creds-tab

        SkyPilot API server uses the same credentials as the :ref:`CoreWeave CAIOS installation <coreweave-caios-installation>` to authenticate with CoreWeave Object Storage.

        Once you have the credentials configured locally, you can store them in a Kubernetes secret:

        .. code-block:: bash

            kubectl create secret generic coreweave-credentials \
              --namespace $NAMESPACE \
              --from-file=cw.config=$HOME/.coreweave/cw.config \
              --from-file=cw.credentials=$HOME/.coreweave/cw.credentials

        When installing or upgrading the Helm chart, enable CoreWeave CAIOS credentials by setting ``coreweaveCredentials.enabled=true``:

        .. code-block:: bash

            # --reuse-values keeps the Helm chart values set in the previous step
            helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
              --namespace $NAMESPACE \
              --reuse-values \
              --set coreweaveCredentials.enabled=true

        .. dropdown:: Use existing CoreWeave CAIOS credentials

            You can also set the following values to use a secret that already contains your CoreWeave CAIOS credentials:

            .. code-block:: bash

                # TODO: replace with your secret name
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set coreweaveCredentials.enabled=true \
                    --set coreweaveCredentials.coreweaveSecretName=your_secret_name

        .. dropdown:: Update CoreWeave CAIOS credentials

           After CoreWeave CAIOS credentials are enabled, you can update the credentials file in ``coreweave-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic coreweave-credentials-new \
                    --namespace $NAMESPACE \
                    --from-file=cw.config=$HOME/.coreweave/cw.config \
                    --from-file=cw.credentials=$HOME/.coreweave/cw.credentials

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set coreweaveCredentials.coreweaveSecretName=coreweave-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret coreweave-credentials
                  kubectl create secret generic coreweave-credentials \
                    --namespace $NAMESPACE \
                    --from-file=cw.config=$HOME/.coreweave/cw.config \
                    --from-file=cw.credentials=$HOME/.coreweave/cw.credentials

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.coreweave/cw.config
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.coreweave/cw.credentials

    .. tab-item:: DigitalOcean
        :sync: digitalocean-creds-tab

        SkyPilot API server use **API key** to authenticate with DigitalOcean. To configure DigitalOcean access,
        follow the `instructions <https://docs.digitalocean.com/reference/api/create-personal-access-token/#creating-a-token>`_
        provided by DigitalOcean.

        Once the key is generated, create a Kubernetes secret to store it:

        .. code-block:: bash

            kubectl create secret generic digitalocean-credentials \
              --namespace $NAMESPACE \
              --from-literal api_key=YOUR_API_KEY

        When installing or upgrading the Helm chart, enable DigitalOcean credentials by
        setting ``digitaloceanCredentials.enabled=true``

        .. dropdown:: Use existing DigitalOcean credentials

            You can also set the following values to use a secret that already contains your DigitalOcean API key:

            .. code-block:: bash

                # TODO: replace with your secret name
                # if secret name is not provided, secret name defaults to `digitalocean-credentials`
                helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set digitaloceanCredentials.enabled=true \
                    --set digitaloceanCredentials.digitaloceanSecretName=your_secret_name

        .. dropdown:: Update DigitalOcean credentials

           After DigitalOcean credentials are enabled, you can update the API key in ``digitalocean-credentials`` using either approach:

           1. Create a new secret with a new name:

              .. code-block:: bash

                  kubectl create secret generic digitalocean-credentials-new \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY_NEW

              Then point Helm to the new secret name:

              .. code-block:: bash

                  helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
                    --namespace $NAMESPACE \
                    --reuse-values \
                    --set digitaloceanCredentials.digitaloceanSecretName=digitalocean-credentials-new

           2. Replace the existing secret in place, then restart the API server:

              .. code-block:: bash

                  kubectl delete secret digitalocean-credentials
                  kubectl create secret generic digitalocean-credentials \
                    --namespace $NAMESPACE \
                    --from-literal api_key=YOUR_API_KEY

              Restart the API server:

              .. code-block:: bash

                  kubectl rollout restart deployment/$RELEASE_NAME-api-server -n $NAMESPACE

              Verify the updated credentials in the API server pod:

              .. code-block:: bash

                  # The NAMESPACE and RELEASE_NAME should be consistent with the API server deployment
                  API_SERVER_POD_NAME=$(kubectl get pods -n $NAMESPACE -l app=${RELEASE_NAME}-api -o jsonpath='{.items[0].metadata.name}')
                  kubectl exec $API_SERVER_POD_NAME -n $NAMESPACE -- cat /root/.config/doctl/config.yaml

    .. tab-item:: Other clouds
        :sync: other-clouds-tab

        You can manually configure the credentials for other clouds by `kubectl exec` into the API server pod after it is deployed and running the relevant :ref:`installation commands<installation>`.

        Note that manually configured credentials will not be persisted across API server restarts.

        Support for configuring other clouds through secrets is coming soon!


Optional: Set up OAuth
----------------------

In addition to basic HTTP authentication, SkyPilot also supports using OAuth2 to securely authenticate users.

Refer to :ref:`Setup OAuth for SkyPilot API Server <api-server-oauth>` for detailed instructions on common OAuth2 providers, such as :ref:`Okta <oauth-okta>` or Google Workspace.

.. _api-server-persistence-db:

Optional: Back the API server with a persistent database
--------------------------------------------------------

The API server can optionally be configured with a PostgreSQL database to persist state. It can be an externally managed database.

If a persistent DB is not specified, the API server uses a Kubernetes persistent volume to persist state.

.. note::

  Database configuration must be set in the Helm deployment.

.. dropdown:: Configure PostgreSQL with Helm deployment during the first deployment

    **Option 1: Set the DB connection URI in helm values**

    Set :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>` to ``postgresql://<username>:<password>@<host>:<port>/<database>`` in the helm values:


    .. code-block:: bash

        # --reuse-values keeps the Helm chart values set in the previous step
        helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
        --namespace $NAMESPACE \
        --reuse-values \
        --set apiService.dbConnectionString=postgresql://<username>:<password>@<host>:<port>/<database>

    **Option 2: Set the DB connection URI via Kubernetes secret**

    (available on nightly version 20250626 and later)

    Create a Kubernetes secret that contains the DB connection URI:

    .. code-block:: bash

        kubectl create secret generic skypilot-db-connection-uri \
          --namespace $NAMESPACE \
          --from-literal connection_string=postgresql://<username>:<password>@<host>:<port>/<database>


    When installing or upgrading the Helm chart, set the ``dbConnectionUri`` to the secret name:

    .. code-block:: bash

        helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
          --namespace $NAMESPACE \
          --reuse-values \
          --set apiService.dbConnectionSecretName=skypilot-db-connection-uri

    You can also directly set this value in the ``values.yaml`` file, e.g.:

    .. code-block:: yaml

        apiService:
          dbConnectionSecretName: skypilot-db-connection-uri

    .. note::

        Once :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>` or :ref:`apiService.dbConnectionSecretName <helm-values-apiService-dbConnectionSecretName>` is specified, no other SkyPilot configuration can be specified in the helm chart. That is, :ref:`apiService.config <helm-values-apiService-config>` must be ``null``. To set any other SkyPilot configuration, see :ref:`sky-api-server-config`.

.. _sky-api-server-config:

Optional: Setting the SkyPilot config
--------------------------------------

To modify your SkyPilot config, use the SkyPilot dashboard: ``http://<api-server-url>/dashboard/config``.

.. image:: ../../images/workspaces/config.png


.. dropdown:: Set the config with helm deployment during the first deployment

    The Helm chart supports setting the global SkyPilot config YAML file on the API server when the API server is deployed for the first time. The config file is mounted as ``~/.sky/config.yaml`` in the API server container.

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
        # --reuse-values keeps the Helm chart values set in the previous step
        helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
        --namespace $NAMESPACE \
        --reuse-values \
        --set-file apiService.config=config.yaml

    You can also directly set config values in the ``values.yaml`` file, e.g.:

    .. code-block:: yaml

        apiService:
          config: |
            allowed_clouds:
            - aws
            - kubernetes


    .. note::

        ``apiService.config`` will be IGNORED during an ``helm upgrade`` if there is an existing config, due to the potential accidental loss of existing config. Use the SkyPilot dashboard instead.

    .. note::

        If remote database is configured (by setting either
        :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>`
        or :ref:`apiService.dbConnectionSecretName <helm-values-apiService-dbConnectionSecretName>`),
        Skypilot configuration cannot be specified in the helm chart.
        Use the dashboard once the API server is deployed to set the config.

Optional: Set up GPU monitoring and metrics
-------------------------------------------

SkyPilot dashboard can be optionally configured to expose GPU metrics and API server metrics.

.. raw:: html

   <div style="display: flex; gap: 20px; margin: 10px auto; justify-content: center; max-width: 1200px; align-items: end;">
     <div style="flex: 1; text-align: center; display: flex; flex-direction: column; height: 350px;">
       <div style="flex: 1; display: flex; align-items: center; justify-content: center;">
         <img src="../../_images/api-srv-metrics.jpg" alt="API Server Metrics Dashboard" style="width: 100%; max-width: 600px;">
       </div>
       <p style="margin-top: 5px; margin-bottom: 0;">API Server Metrics Dashboard</p>
     </div>
     <div style="flex: 1; text-align: center; display: flex; flex-direction: column; height: 350px;">
       <div style="flex: 1; display: flex; align-items: center; justify-content: center;">
         <img src="../../_images/gpu-metrics.png" alt="GPU Metrics Dashboard" style="width: 100%; max-width: 600px;">
       </div>
       <p style="margin-top: 5px; margin-bottom: 0;">GPU Metrics Dashboard</p>
     </div>
   </div>

To enable metrics, set ``apiService.metrics.enabled=true``, ``prometheus.enabled=true`` and ``grafana.enabled=true`` in the Helm chart.

.. code-block:: bash

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set apiService.metrics.enabled=true \
      --set prometheus.enabled=true \
      --set grafana.enabled=true


For detailed setup instructions (including how to set up external Prometheus and Grafana), see:

* :ref:`API Server Metrics Setup <api-server-metrics-setup>`
* :ref:`GPU Metrics Setup <api-server-gpu-metrics-setup>`


Optional: Set up server-side debug logging
------------------------------------------

Client-side debug logging can be turned on for individual requests by setting the
``SKYPILOT_DEBUG`` environment variable to ``1`` when submitting a request, e.g.

.. code-block:: bash

    SKYPILOT_DEBUG=1 sky status

To enable debug logging for all requests on server side, set
``SKYPILOT_SERVER_ENABLE_REQUEST_DEBUG_LOGGING`` to ``true`` in the Helm values:

.. code-block:: bash

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
      --namespace $NAMESPACE \
      --reuse-values \
      --set-string 'apiService.extraEnvs[0].name=SKYPILOT_SERVER_ENABLE_REQUEST_DEBUG_LOGGING' \
      --set-string 'apiService.extraEnvs[0].value=true'


Debug level logs for each request are saved to ``~/sky_logs/request_debug/<request_id>.log`` on the API server.
Server-side debug logging does not affect output seen by the clients.

Upgrade the API server
-----------------------

Refer to :ref:`sky-api-server-upgrade` for how to upgrade the API server.



Uninstall
---------

To uninstall the API server, run:

.. code-block:: bash

    helm uninstall $RELEASE_NAME --namespace $NAMESPACE --wait

This will delete the API server and all associated resources. ``--wait`` ensures that all the resources of SkyPilot API server are deleted before the command returns.


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

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel -f values.yaml


Additional setup for EKS
^^^^^^^^^^^^^^^^^^^^^^^^

To support persistent storage for the API server's state, we need a storage class that supports persistent volumes. If you already have a storage class that supports persistent volumes, you can skip the following steps.

We will use the `Amazon EBS CSI driver <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_ to create a storage class that supports persistent volumes backed by Amazon EBS. You can also use other storage classes that support persistent volumes, such as `EFS <https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html>`_.

The steps below are based on the `official documentation <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_. Please follow the official documentation to adapt the steps to your cluster.

1. Make sure OIDC is enabled for your cluster. Follow the steps `here <https://docs.aws.amazon.com/eks/latest/userguide/enable-iam-roles-for-service-accounts.html>`_.

   a. You will need to create and bind an IAM role which has permissions to create EBS volumes. See `instructions here <https://docs.aws.amazon.com/eks/latest/userguide/associate-service-account-role.html>`_.

2. Install the `Amazon EBS CSI driver <https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html>`_. The recommended method is through creating an EKS add-on.

Once the EBS CSI driver is installed, the default ``gp2`` storage class will be backed by EBS volumes.

.. _sky-api-server-admin-policy:

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

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel -f values.yaml

.. _minimum-permissions-in-helm:

Setting minimum permissions in helm deployment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In helm deployment, a set of default permissions are granted to the API server to access the hosting Kubernetes cluster. You can customize the permissions in the following conditions:

* Reduce the RBAC permissions by using ``kubernetes.remote_identity``: by default, the API server creates a service account and RBAC roles to grant permissions to SkyPilot task Pods. This in turn requires the API server to have permissions to manipulate RBAC roles and service accounts. You can disable this by the following steps:

  1. Refer to :ref:`Setting the SkyPilot config <sky-api-server-config>` to set ``kubernetes.remote_identity`` to the service account of API server, which already has the necessary permissions:

     .. code-block:: yaml

        # TODO: replace ${RELEASE_NAME} with the actual release name in deployment step
        kubernetes:
          remote_identity: ${RELEASE_NAME}-api-sa

     .. note::

        If you also grant external Kubernetes cluster permissions to the API server via ``kubernetesCredentials.useKubeconfig``, the same service account with enough permissions must be prepared in these Kubernetes clusters manually.

  2. Set ``rbac.manageRbacPolicies=false`` in helm valuesto disable the RBAC policies:

     .. code-block:: bash

        helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
          --set rbac.manageRbacPolicies=false

* If your use case does not require object storage mounting, you can disable the permissions to manage SkyPilot system components by setting ``rbac.manageSystemComponents=false``:

  .. code-block:: bash

      helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
        --set rbac.manageSystemComponents=false

If you want to use an existing service account and permissions that meet the :ref:`minimum permissions required for SkyPilot<k8s-permissions>` instead of the one managed by Helm, you can disable the creation of RBAC policies and specify the service account name to use:

.. code-block:: bash

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel --reuse-values \
      --set rbac.create=false \
      --set rbac.serviceAccountName=my-existing-service-account


.. _sky-api-server-helm-multiple-deploy:

Reusing ingress controller for API server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default, the SkyPilot helm chart will deploy a new ingress-nginx controller when installing the API server. However, the ingress-nginx controller has some cluster-scope resources that will cause conflicts between multiple installations by default. It is recommended to reuse an existing ingress controller if you want to deploy multiple API servers in the same Kubernetes cluster.

To reuse an existing ingress controller, you can set :ref:`ingress-nginx.enabled <helm-values-ingress-nginx-enabled>` to ``false`` and set :ref:`ingress.path <helm-values-ingress-path>` to a unique path for the deploying API server. For example:

.. code-block:: bash

    # The first API server, with niginx-ingress controller deployed
    # It is assumed that the first API server is already deployed. If it is not deployed yet,
    # add necessary values instead of specifying --reuse-values
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
        --namespace $NAMESPACE \
        --reuse-values \
        --set ingress.path=/first-server

    # The second API server, reusing the existing ingress controller and using a different path
    ANOTHER_RELEASE_NAME=skypilot2
    ANOTHER_NAMESPACE=skypilot2
    # Replace with your username and password to configure the basic auth credentials for the second API server
    ANOTHER_WEB_USERNAME=skypilot
    ANOTHER_WEB_PASSWORD=yourpassword2
    ANOTHER_AUTH_STRING=$(htpasswd -nb $ANOTHER_WEB_USERNAME $ANOTHER_WEB_PASSWORD)
    # Deploy the API server, either in the same namespace or a different namespace
    helm upgrade --install $ANOTHER_RELEASE_NAME skypilot/skypilot-nightly --devel \
        --namespace $ANOTHER_NAMESPACE \
        --set ingress-nginx.enabled=false \
        --set ingress.path=/second-server \
        --set ingress.authCredentials=$ANOTHER_AUTH_STRING

With the above commands, these two API servers will share the same ingress controller and serves under different paths of the same host. To get the endpoints, follow :ref:`Step 2: Get the API server URL <sky-get-api-server-url>` to get the host from the helm release that has the ingress-nginx controller deployed, and then append the basic auth and path to the host:

.. code-block:: bash

    # HOST was the ingress host we got from Step 2
    $ FIRST_PATH=$(kubectl get ingress ${RELEASE_NAME}-ingress --namespace $NAMESPACE -o jsonpath='{.metadata.annotations.skypilot\.co\/ingress-path}')
    $ FIRST_ENDPOINT=http://${WEB_USERNAME}:${WEB_PASSWORD}@${HOST}${FIRST_PATH}
    $ SECOND_PATH=$(kubectl get ingress ${ANOTHER_RELEASE_NAME}-ingress --namespace $ANOTHER_NAMESPACE -o jsonpath='{.metadata.annotations.skypilot\.co\/ingress-path}')
    $ SECOND_ENDPOINT=http://${ANOTHER_WEB_USERNAME}:${ANOTHER_WEB_PASSWORD}@${HOST}${SECOND_PATH}
    $ echo $FIRST_ENDPOINT
    http://skypilot:yourpassword@1.1.1.1/first-server
    $ echo $SECOND_ENDPOINT
    http://skypilot:yourpassword2@1.1.1.1/second-server

The same approach also applies when you have a ingress-nginx controller deployed before installing the SkyPilot API server:

.. code-block:: bash

    # The first API server, disabling the ingress-nginx controller to reuse the existing one
    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
        --namespace $NAMESPACE \
        --set ingress-nginx.enabled=false \
        --set ingress.path=/skypilot

It is a good practice to specify a unique :ref:`ingress.path <helm-values-ingress-path>` too in this case, to avoid conflicts with other backends hosted on the same ingress controller.

.. _sky-api-server-custom-ingress:

Use custom ingress controller
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default, the SkyPilot helm chart will deploy a new ingress-nginx controller when installing the API server. However, you can use a custom ingress controller by disabling the creation of nginx ingress controller and setting :ref:`ingress.ingressClassName <helm-values-ingress-ingressclassname>` to the ingress class name of your controller. In addition, most of the ingress controllers support customizing behavior by setting annotations on the ingress resource. You can set :ref:`ingress.annotations <helm-values-ingress-annotations>` in the helm values to pass annotations to the ingress resource. Here is an example of using a custom ingress controller:

.. code-block:: bash

    helm upgrade --install $RELEASE_NAME skypilot/skypilot-nightly --devel \
        --namespace $NAMESPACE \
        --reuse-values \
        --set ingress-nginx.enabled=false \
        --set ingress.ingressClassName=custom-ingress-class \
        --set ingress.annotations.custom-ingress-annotation=custom-ingress-annotation-value

.. note::

    .. TODO(aylei): document service account token based authentication and proxy auth for custom ingress controllers.

    :ref:`Basic auth on ingress <helm-values-ingress-authcredentials>` is only supported when using ingress-nginx controller. Consider using :ref:`OAuth2 <api-server-oauth>` to protect your API server instead.


.. _sky-api-server-cloud-deploy:

Alternative: Deploy on cloud VMs
--------------------------------

.. note::

    VM deployment does not offer failover and graceful upgrading supports. We recommend to use Helm deployment :ref:`sky-api-server-deploy` in production environments.

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


.. toctree::
   :hidden:

    API server metrics monitoring <examples/api-server-metrics-setup>
    GPU metrics monitoring <examples/api-server-gpu-metrics-setup>
    Advanced: Cross-Cluster State Persistence <examples/api-server-persistence>
    Example: Deploy on GKE, GCP, and Nebius with Okta <examples/example-deploy-gke-nebius-okta>
    Example: Deploy SkyPilot API Server in Docker <examples/api-server-in-docker>
    Example: Deploy on GKE with Cloud SQL <examples/example-deploy-gcp-cloud-sql>

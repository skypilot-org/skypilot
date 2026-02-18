.. _helm-values-spec:

SkyPilot API Server Helm Chart Values
=====================================

The SkyPilot API server helm chart provides typical `helm values <https://helm.sh/docs/chart_template_guide/values_files/>`_ as configuration entries. Configuration values can be passed in two ways when installing the chart:

* ``--values`` (or ``-f``): Specify a YAML file with overrides.

  .. code-block:: bash

      cat <<EOF > values.yaml
      apiService:
        image: berkeleyskypilot/skypilot:0.9.2
      EOF

      helm install $RELEASE_NAME skypilot/skypilot-nightly --devel --values values.yaml

* ``--set``: Specify overrides on the command line.

  .. code-block:: bash

      helm install $RELEASE_NAME skypilot/skypilot-nightly --set apiService.image="berkeleyskypilot/skypilot:0.9.2"

Values
------

Below is the available helm value keys and the default value of each key:

.. parsed-literal::

  :ref:`global <helm-values-global>`:
    :ref:`imageRegistry <helm-values-global-imageRegistry>`: null
    :ref:`imagePullSecrets <helm-values-global-imagePullSecrets>`: null
    :ref:`extraEnvs <helm-values-global-extraEnvs>`: null
  :ref:`fullnameOverride <helm-values-fullnameOverride>`: null
  :ref:`apiService <helm-values-apiService>`:
    :ref:`image <helm-values-apiService-image>`: berkeleyskypilot/skypilot-nightly:latest
    :ref:`upgradeStrategy <helm-values-apiService-upgradeStrategy>`: Recreate
    :ref:`replicas <helm-values-apiService-replicas>`: 1
    :ref:`enableUserManagement <helm-values-apiService-enableUserManagement>`: false
    :ref:`initialBasicAuthCredentials <helm-values-apiService-initialBasicAuthCredentials>`: "skypilot:$apr1$c1h4rNxt$2NnL7dIDUV0tWsnuNMGSr/"
    :ref:`initialBasicAuthSecret <helm-values-apiService-initialBasicAuthSecret>`: null
    :ref:`authUserHeaderName <helm-values-apiService-authUserHeaderName>`: null
    :ref:`preDeployHook <helm-values-apiService-preDeployHook>`: \|-
      # Run commands before deploying the API server, e.g. installing an admin
      # policy. Remember to set the admin policy in the config section below.

      echo "Pre-deploy hook"

      # Uncomment the following lines to install the admin policy

      # echo "Installing admin policy"
      # pip install git+https://github.com/michaelvll/admin-policy-examples
    :ref:`config <helm-values-apiService-config>`: null
    :ref:`dbConnectionSecretName <helm-values-apiService-dbConnectionSecretName>`: null
    :ref:`dbConnectionString <helm-values-apiService-dbConnectionString>`: null
    :ref:`sshNodePools <helm-values-apiService-sshNodePools>`: null
    :ref:`sshKeySecret <helm-values-apiService-sshKeySecret>`: null
    :ref:`skipResourceCheck <helm-values-apiService-skipResourceCheck>`: false
    :ref:`resources <helm-values-apiService-resources>`:
      requests:
        cpu: "4"
        memory: "8Gi"
      limits:
        cpu: "4"
        memory: "8Gi"
    :ref:`skypilotDev <helm-values-apiService-skypilotDev>`: false
    :ref:`metrics <helm-values-apiService-metrics>`:
      :ref:`enabled <helm-values-apiService-metrics-enabled>`: false
      :ref:`port <helm-values-apiService-metrics-port>`: 9090
    :ref:`terminationGracePeriodSeconds <helm-values-apiService-terminationGracePeriodSeconds>`: 60
    :ref:`annotations <helm-values-apiService-annotations>`: null
    :ref:`extraEnvs <helm-values-apiService-extraEnvs>`: null
    :ref:`extraVolumes <helm-values-apiService-extraVolumes>`: null
    :ref:`extraVolumeMounts <helm-values-apiService-extraVolumeMounts>`: null
    :ref:`sidecarContainers <helm-values-apiService-sidecarContainers>`: null
    :ref:`logs <helm-values-apiService-logs>`:
      :ref:`retention <helm-values-apiService-logs-retention>`:
        :ref:`enabled <helm-values-apiService-logs-retention-enabled>`: false
        :ref:`size <helm-values-apiService-logs-retention-size>`: 10M
    :ref:`imagePullPolicy <helm-values-apiService-imagePullPolicy>`: Always

  :ref:`auth <helm-values-auth>`:
    :ref:`oauth <helm-values-auth-oauth>`:
      :ref:`enabled <helm-values-auth-oauth-enabled>`: false
      :ref:`oidc-issuer-url <helm-values-auth-oauth-oidc-issuer-url>`: null
      :ref:`client-id <helm-values-auth-oauth-client-id>`: ""
      :ref:`client-secret <helm-values-auth-oauth-client-secret>`: ""
      :ref:`client-details-from-secret <helm-values-auth-oauth-client-details-from-secret>`: ""
      :ref:`email-domain <helm-values-auth-oauth-email-domain>`: "*"
      :ref:`session-store-type <helm-values-auth-oauth-session-store-type>`: "redis"
      :ref:`redis-url <helm-values-auth-oauth-redis-url>`: null
      :ref:`redis-secret <helm-values-auth-oauth-redis-secret>`: null
      :ref:`cookie-refresh <helm-values-auth-oauth-cookie-refresh>`: null
      :ref:`cookie-expire <helm-values-auth-oauth-cookie-expire>`: null
    :ref:`serviceAccount <helm-values-auth-serviceAccount>`:
      :ref:`enabled <helm-values-auth-serviceAccount-enabled>`: null
    :ref:`externalProxy <helm-values-auth-externalProxy>`:
      :ref:`enabled <helm-values-auth-externalProxy-enabled>`: false
      :ref:`headerName <helm-values-auth-externalProxy-headerName>`: 'X-Auth-Request-Email'
      :ref:`headerFormat <helm-values-auth-externalProxy-headerFormat>`: 'plaintext'
      :ref:`jwtIdentityClaim <helm-values-auth-externalProxy-jwtIdentityClaim>`: 'sub'

  :ref:`storage <helm-values-storage>`:
    :ref:`enabled <helm-values-storage-enabled>`: true
    :ref:`storageClassName <helm-values-storage-storageClassName>`: ""
    :ref:`accessMode <helm-values-storage-accessMode>`: ReadWriteOnce
    :ref:`size <helm-values-storage-size>`: 10Gi
    :ref:`selector <helm-values-storage-selector>`: {}
    :ref:`volumeName <helm-values-storage-volumeName>`: ""
    :ref:`annotations <helm-values-storage-annotations>`: {}

  :ref:`ingress <helm-values-ingress>`:
    :ref:`enabled <helm-values-ingress-enabled>`: true
    :ref:`unified <helm-values-ingress-unified>`: false
    :ref:`authSecret <helm-values-ingress-authSecret>`: null
    :ref:`authCredentials <helm-values-ingress-authCredentials>`: null
    :ref:`host <helm-values-ingress-host>`: null
    :ref:`path <helm-values-ingress-path>`: '/'
    :ref:`ingressClassName <helm-values-ingress-ingressClassName>`: nginx
    :ref:`annotations <helm-values-ingress-annotations>`: null
    # Deprecated: use auth.oauth instead.
    :ref:`oauth2-proxy <helm-values-ingress-oauth2-proxy>`:
      :ref:`enabled <helm-values-ingress-oauth2-proxy-enabled>`: false
      :ref:`oidc-issuer-url <helm-values-ingress-oauth2-proxy-oidc-issuer-url>`: null
      :ref:`client-id <helm-values-ingress-oauth2-proxy-client-id>`: ""
      :ref:`client-secret <helm-values-ingress-oauth2-proxy-client-secret>`: ""
      :ref:`client-details-from-secret <helm-values-ingress-oauth2-proxy-client-details-from-secret>`: ""
      :ref:`image <helm-values-ingress-oauth2-proxy-image>`: "quay.io/oauth2-proxy/oauth2-proxy:v7.9.0"
      :ref:`use-https <helm-values-ingress-oauth2-proxy-use-https>`: false
      :ref:`email-domain <helm-values-ingress-oauth2-proxy-email-domain>`: "*"
      :ref:`session-store-type <helm-values-ingress-oauth2-proxy-session-store-type>`: "redis"
      :ref:`redis-url <helm-values-ingress-oauth2-proxy-redis-url>`: null
      :ref:`cookie-refresh <helm-values-ingress-oauth2-proxy-cookie-refresh>`: null
      :ref:`cookie-expire <helm-values-ingress-oauth2-proxy-cookie-expire>`: null
    :ref:`tls <helm-values-ingress-tls>`:
      :ref:`enabled <helm-values-ingress-tls-enabled>`: false

  :ref:`ingress-nginx <helm-values-ingress-nginx>`:
    :ref:`enabled <helm-values-ingress-nginx-enabled>`: true
    :ref:`controller <helm-values-ingress-nginx-controller>`:
      service:
        type: LoadBalancer
        annotations:
          service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
          cloud.google.com/l4-rbs: "enabled"
          service.beta.kubernetes.io/port_443_health-probe_protocol: "TCP"
          service.beta.kubernetes.io/port_80_health-probe_protocol: "TCP"
      config:
        http-snippet: |
          map $http_upgrade $connection_upgrade {
              default upgrade;
              ''      close;
          }

  :ref:`rbac <helm-values-rbac>`:
    :ref:`create <helm-values-rbac-create>`: true
    :ref:`serviceAccountName <helm-values-rbac-serviceAccountName>`: ""
    :ref:`namespaceRules <helm-values-rbac-namespaceRules>`:
      - apiGroups: [ "" ]
        resources: [ "pods", "pods/status", "pods/exec", "pods/portforward" ]
        verbs: [ "*" ]
      - apiGroups: [ "" ]
        resources: [ "services" ]
        verbs: [ "*" ]
      - apiGroups: [ "" ]
        resources: [ "secrets" ]
        verbs: [ "*" ]
      - apiGroups: [ "" ]
        resources: [ "events" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "" ]
        resources: [ "configmaps" ]
        verbs: [ "get", "patch" ]
      - apiGroups: ["apps"]
        resources: ["deployments", "deployments/status"]
        verbs: ["*"]
      - apiGroups: [""]
        resources: ["persistentvolumeclaims"]
        verbs: ["*"]
    :ref:`clusterRules <helm-values-rbac-clusterRules>`:
      - apiGroups: [ "" ]
        resources: [ "nodes" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "" ]
        resources: [ "pods" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "node.k8s.io" ]
        resources: [ "runtimeclasses" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "networking.k8s.io" ]
        resources: [ "ingressclasses" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [""]
        resources: ["services"]
        verbs: ["list", "get"]
    :ref:`manageRbacPolicies <helm-values-rbac-manageRbacPolicies>`: true
    :ref:`manageSystemComponents <helm-values-rbac-manageSystemComponents>`: true
    :ref:`serviceAccountAnnotations <helm-values-rbac-serviceAccountAnnotations>`: null

  :ref:`kubernetesCredentials <helm-values-kubernetesCredentials>`:
    :ref:`useApiServerCluster <helm-values-kubernetesCredentials-useApiServerCluster>`: true
    :ref:`useKubeconfig <helm-values-kubernetesCredentials-useKubeconfig>`: false
    :ref:`kubeconfigSecretName <helm-values-kubernetesCredentials-kubeconfigSecretName>`: kube-credentials
    :ref:`inclusterNamespace <helm-values-kubernetesCredentials-inclusterNamespace>`: null

  :ref:`awsCredentials <helm-values-awsCredentials>`:
    :ref:`enabled <helm-values-awsCredentials-enabled>`: false
    :ref:`awsSecretName <helm-values-awsCredentials-awsSecretName>`: aws-credentials
    :ref:`useCredentialsFile <helm-values-awsCredentials-useCredentialsFile>`: false
    :ref:`accessKeyIdKeyName <helm-values-awsCredentials-accessKeyIdKeyName>`: aws_access_key_id
    :ref:`secretAccessKeyKeyName <helm-values-awsCredentials-secretAccessKeyKeyName>`: aws_secret_access_key

  :ref:`gcpCredentials <helm-values-gcpCredentials>`:
    :ref:`enabled <helm-values-gcpCredentials-enabled>`: false
    :ref:`projectId <helm-values-gcpCredentials-projectId>`: null
    :ref:`gcpSecretName <helm-values-gcpCredentials-gcpSecretName>`: gcp-credentials

  :ref:`r2Credentials <helm-values-r2Credentials>`:
    :ref:`enabled <helm-values-r2Credentials-enabled>`: false
    :ref:`r2SecretName <helm-values-r2Credentials-r2SecretName>`: r2-credentials
  :ref:`runpodCredentials <helm-values-runpodCredentials>`:
    :ref:`enabled <helm-values-runpodCredentials-enabled>`: false
    :ref:`runpodSecretName <helm-values-runpodCredentials-runpodSecretName>`: runpod-credentials

  :ref:`lambdaCredentials <helm-values-lambdaCredentials>`:
    :ref:`enabled <helm-values-lambdaCredentials-enabled>`: false
    :ref:`lambdaSecretName <helm-values-lambdaCredentials-lambdaSecretName>`: lambda-credentials

  :ref:`vastCredentials <helm-values-vastCredentials>`:
    :ref:`enabled <helm-values-vastCredentials-enabled>`: false
    :ref:`vastSecretName <helm-values-vastCredentials-vastSecretName>`: vast-credentials

  :ref:`nebiusCredentials <helm-values-nebiusCredentials>`:
    :ref:`enabled <helm-values-nebiusCredentials-enabled>`: false
    :ref:`tenantId <helm-values-nebiusCredentials-tenantId>`: null
    :ref:`nebiusSecretName <helm-values-nebiusCredentials-nebiusSecretName>`: nebius-credentials

  :ref:`coreweaveCredentials <helm-values-coreweaveCredentials>`:
    :ref:`enabled <helm-values-coreweaveCredentials-enabled>`: false
    :ref:`coreweaveSecretName <helm-values-coreweaveCredentials-coreweaveSecretName>`: coreweave-credentials

  :ref:`digitaloceanCredentials <helm-values-digitaloceanCredentials>`:
    :ref:`enabled <helm-values-digitaloceanCredentials-enabled>`: false
    :ref:`digitaloceanSecretName <helm-values-digitaloceanCredentials-digitaloceanSecretName>`: digitalocean-credentials

  :ref:`extraInitContainers <helm-values-extraInitContainers>`: null

  :ref:`podSecurityContext <helm-values-podSecurityContext>`: {}

  :ref:`securityContext <helm-values-securityContext>`:
    :ref:`capabilities <helm-values-securityContext-capabilities>`:
      drop:
      - ALL
    :ref:`allowPrivilegeEscalation <helm-values-securityContext-allowPrivilegeEscalation>`: false

  :ref:`runtimeClassName <helm-values-runtimeClassName>`: null

  :ref:`prometheus <helm-values-prometheus>`:
    :ref:`enabled <helm-values-prometheus-enabled>`: false

  :ref:`grafana <helm-values-grafana>`:
    :ref:`enabled <helm-values-grafana-enabled>`: false

Fields
----------

.. _helm-values-global:

``global``
~~~~~~~~~~

Global configuration for all components in the chart.

.. _helm-values-global-imageRegistry:

``global.imageRegistry``
^^^^^^^^^^^^^^^^^^^^^^^^

Override the image registry host for every container image produced by the chart. Images that omit a registry (implicitly ``docker.io``) are prefixed with ``global.imageRegistry`` and images that already declare a registry have that host replaced with ``global.imageRegistry``.

For example, if ``global.imageRegistry`` is set to ``registry.example.com/custom``, the following images will be replaced:

.. code-block:: yaml

  berkeleyskypilot/skypilot:latest
  # becomes
  registry.example.com/custom/berkeleyskypilot/skypilot:latest

  quay.io/oauth2-proxy/oauth2-proxy:v7.9.0
  # becomes
  registry.example.com/custom/oauth2-proxy/oauth2-proxy:v7.9.0

.. note::

  This override will only be applied to images defined in the SkyPilot chart; bundled subcharts such as ``ingress-nginx`` keep their own registry configuration. Refer to the documentations of subcharts for how to change them.

Default: ``null``

.. code-block:: yaml

  global:
    imageRegistry: registry.example.com/custom


.. _helm-values-global-imagePullSecrets:

``global.imagePullSecrets``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Specify imagePullSecrets for all components in the chart.

Default: ``null``

.. code-block:: yaml

  global:
    imagePullSecrets:
      - name: my-registry-credentials

.. _helm-values-global-extraEnvs:

``global.extraEnvs``
^^^^^^^^^^^^^^^^^^^^

Specify extra environment variables to set on all components in the chart.

Default: ``null``

.. code-block:: yaml

  global:
    extraEnvs:
      - name: HTTP_PROXY
        value: http://proxy.example.com


.. _helm-values-fullnameOverride:

``fullnameOverride``
~~~~~~~~~~~~~~~~~~~~

Override the full name used for all resources created by this chart. By default, names are derived from the Helm release name (``Release.Name``). Set ``fullnameOverride`` to enforce a specific base name when coordinating multiple environments.

Note that sub charts will not inherit the top-level ``fullnameOverride`` value, so you need to set it for each sub chart if you want to use a different base name for each sub chart, and the ``fullnameOverride`` of prometheus must be consistent with the top-level ``fullnameOverride`` to make sure the scrape target is consistent.

.. code-block:: yaml

  fullenameOverride: custom-name
  prometheus:
    # Must use the same fullnameOverride as top-level
    fullnameOverride: custom-name
  ingress-nginx:
    fullnameOverride: other-name
  grafana:
    fullnameOverride: other-name

Default: ``null``

.. code-block:: yaml

  fullnameOverride: custom-name
  prometheus:
    fullnameOverride: custom-name

.. _helm-values-apiService:

``apiService``
~~~~~~~~~~~~~~

Configuration for the SkyPilot API server deployment.

.. _helm-values-apiService-image:

``apiService.image``
^^^^^^^^^^^^^^^^^^^^

Docker image to use for the API server. The default value is depending on the chart you are using:

- Stable release of the chart(``skypilot/skypilot``): the same stable release of SkyPilot will be used by default, i.e. ``berkeleyskypilot/skypilot:$CHART_VERSION``.
- Nightly release of the chart(``skypilot/skypilot-nightly``): the same nightly build of SkyPilot will be used by default, i.e. ``berkeleyskypilot/skypilot-nightly:$CHART_VERSION``.
- Installing from `source <https://github.com/skypilot-org/skypilot/tree/master/charts/skypilot>`_: the latest nightly build of SkyPilot will be used by default, i.e. ``berkeleyskypilot/skypilot-nightly:latest``.

To use a specific release version, set the ``image`` value to the desired version:

.. code-block:: yaml

  apiService:
    image: berkeleyskypilot/skypilot:0.10.0

To use a nightly build, find the desired nightly version on `pypi <https://pypi.org/project/skypilot-nightly/#history>`_ and update the ``image`` value:

.. code-block:: yaml

  apiService:
    # Replace 1.0.0.devYYYYMMDD with the desired nightly version
    image: berkeleyskypilot/skypilot-nightly:1.0.0.devYYYYMMDD


.. _helm-values-apiService-imagePullPolicy:

``apiService.imagePullPolicy``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Image pull policy applied to the API server containers. Accepts the standard Kubernetes values (``Always``, ``IfNotPresent``, ``Never``). Use ``IfNotPresent`` when caching images locally is preferred.

Default: ``"Always"``

.. code-block:: yaml

  apiService:
    imagePullPolicy: Always

.. _helm-values-apiService-upgradeStrategy:

``apiService.upgradeStrategy``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Upgrade strategy for the API server deployment. Available options are:

- ``Recreate``: Delete the old pod first and create a new one (has downtime).
- ``RollingUpdate``: Create a new pod first, wait for it to be ready, then delete the old one (zero downtime).

When set to ``RollingUpdate``, an external database must be configured via :ref:`apiService.dbConnectionSecretName <helm-values-apiService-dbConnectionSecretName>` or :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>`.

For persistent storage with RollingUpdate:

- If :ref:`storage.enabled=true <helm-values-storage-enabled>`, use :ref:`storage.accessMode <helm-values-storage-accessMode>` =ReadWriteMany with an RWX-capable storage class (e.g., NFS-backed storage). This sets the ``SKYPILOT_API_SERVER_STORAGE_ENABLED`` environment variable, ensuring managed job logs and file mounts persist across rolling updates.
- If ``storage.enabled=false``, file mounts and logs will be lost on pod restart. Consider configuring ``jobs.bucket`` in the SkyPilot config to persist file mounts to cloud storage.

Default: ``"Recreate"``

.. code-block:: yaml

  apiService:
    upgradeStrategy: Recreate

.. _helm-values-apiService-replicas:

``apiService.replicas``
^^^^^^^^^^^^^^^^^^^^^^^

Number of replicas to deploy for the API server. Replicas > 1 is not well tested and requires a PVC that supports ReadWriteMany.

Default: ``1``

.. code-block:: yaml

  apiService:
    replicas: 1

.. _helm-values-apiService-enableUserManagement:

``apiService.enableUserManagement``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable basic auth and user management in the API server. This is ignored if ``ingress.oauth2-proxy.enabled`` is ``true``.

If enabled, the user can be created, updated, and deleted in the Dashboard, and the basic auth will be done in the API server instead of the ingress controller. In this case, the basic auth configuration ``ingress.authCredentials`` and ``ingress.authSecret`` in the ingress will be ignored.

Default: ``false``

.. code-block:: yaml

  apiService:
    enableUserManagement: false

.. _helm-values-apiService-initialBasicAuthCredentials:

``apiService.initialBasicAuthCredentials``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Initial basic auth credentials for the API server.

The user in the credentials will be used to create a new admin user in the API server, and the password can be updated by the user in the Dashboard.

If both ``initialBasicAuthCredentials`` and ``initialBasicAuthSecret`` are set, ``initialBasicAuthSecret`` will be used. They are only used when ``enableUserManagement`` is true.

Default: ``"skypilot:$apr1$c1h4rNxt$2NnL7dIDUV0tWsnuNMGSr/"``

.. code-block:: yaml

  apiService:
    initialBasicAuthCredentials: "skypilot:$apr1$c1h4rNxt$2NnL7dIDUV0tWsnuNMGSr/"

.. _helm-values-apiService-initialBasicAuthSecret:

``apiService.initialBasicAuthSecret``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Initial basic auth secret for the API server. If not specified, a new secret will be created using ``initialBasicAuthCredentials``.

To create a new secret, you can use the following command:

.. code-block:: bash

  WEB_USERNAME=skypilot
  WEB_PASSWORD=skypilot
  AUTH_STRING=$(htpasswd -nb $WEB_USERNAME $WEB_PASSWORD)
  NAMESPACE=skypilot
  kubectl create secret generic initial-basic-auth \
    --from-literal=auth=$AUTH_STRING \
    -n $NAMESPACE

Default: ``null``

.. code-block:: yaml

  apiService:
    initialBasicAuthSecret: null

.. _helm-values-apiService-authUserHeaderName:

``apiService.authUserHeaderName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Custom header name for user authentication with auth proxies. This overrides the default ``X-Auth-Request-Email`` header.

This setting is useful when integrating with auth proxies that use different header names for user identification, such as ``X-Remote-User``, ``X-Auth-User``, or custom headers specific to your organization's auth infrastructure.

Default: ``null`` (uses ``X-Auth-Request-Email``)

.. code-block:: yaml

  apiService:
    authUserHeaderName: X-Custom-User-Header

.. _helm-values-apiService-preDeployHook:

``apiService.preDeployHook``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Commands to run before deploying the API server (e.g., install :ref:`admin policy <advanced-policy-config>`).

Default: see the yaml below.

.. code-block:: yaml

  apiService:
    preDeployHook: |-
      # Run commands before deploying the API server, e.g. installing an admin
      # policy. Remember to set the admin policy in the config section below.
      echo "Pre-deploy hook"

      # Uncomment the following lines to install the admin policy
      # echo "Installing admin policy"
      # pip install git+https://github.com/michaelvll/admin-policy-examples

.. _helm-values-apiService-config:

``apiService.config``
^^^^^^^^^^^^^^^^^^^^^

Content of the `SkyPilot config.yaml <https://docs.skypilot.co/en/latest/reference/config.html>`_ to set on the API server. Set to ``null`` to use an empty config. Refer to :ref:`setting the SkyPilot config <sky-api-server-config>` for more details.

Default: ``null``

.. code-block:: yaml

  apiService:
    config: |-
      allowed_clouds:
        - aws
        - gcp

.. _helm-values-apiService-dbConnectionSecretName:

``apiService.dbConnectionSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the database connection string for the API server. This is used to configure an external database for the API server.

If either this field or :ref:`apiService.dbConnectionString <helm-values-apiService-dbConnectionString>` is set, :ref:`apiService.config <helm-values-apiService-config>` must be ``null``. Refer to the :ref:`API server deployment guide <sky-api-server-helm-deploy-command>` for more details on configuring an external database.
Name of the secret containing the database connection string for the API server. If this field is set, ``config`` must be null.

Default: ``null``

.. code-block:: yaml

  apiService:
    dbConnectionSecretName: my-db-connection-secret

.. _helm-values-apiService-dbConnectionString:

``apiService.dbConnectionString``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Database connection string for the API server. This is a shortcut for setting the database connection string directly instead of using a secret.

If either this field or :ref:`apiService.dbConnectionSecretName <helm-values-apiService-dbConnectionSecretName>` is set, :ref:`apiService.config <helm-values-apiService-config>` must be ``null``. Refer to the :ref:`API server deployment guide <sky-api-server-helm-deploy-command>` for more details on configuring an external database.

Default: ``null``

.. code-block:: yaml

  apiService:
    dbConnectionString: "postgresql://user:password@host:port/database"

.. _helm-values-apiService-enableServiceAccounts:

``apiService.enableServiceAccounts``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable service accounts in the API server.

Deprecated: use :ref:`auth.serviceAccount.enabled <helm-values-auth-serviceAccount-enabled>` instead.

Default: ``true``


.. _helm-values-apiService-sshNodePools:

``apiService.sshNodePools``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Content of the ``~/.sky/ssh_node_pools.yaml`` to set on the API server. Set to ``null`` to use an empty ssh node pools. Refer to :ref:`Deploy SkyPilot on existing machines <existing-machines>` for more details.

Default: ``null``

.. code-block:: yaml

  apiService:
    sshNodePools: |-
      my-cluster:
        hosts:
          - 1.2.3.4
          - 1.2.3.5

      my-box:
        hosts:
          - hostname_in_ssh_config

.. _helm-values-apiService-sshKeySecret:

``apiService.sshKeySecret``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Optional secret that contains SSH identity files to the API server to use, all the entries in the secret will be mounted to ``~/.ssh/`` directory in the API server. Refer to :ref:`Deploy SkyPilot on existing machines <existing-machines>` for more details.

Default: ``null``

.. code-block:: yaml

  apiService:
    sshKeySecret: my-ssh-key-secret

The content of the secret should be like:

.. code-block:: yaml

  apiVersion: v1
  kind: Secret
  metadata:
    name: my-ssh-key-secret
  data:
    id_rsa: <secret-content>


.. _helm-values-apiService-skipResourceCheck:

``apiService.skipResourceCheck``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Skip resource check for the API server (not recommended for production), refer to :ref:`tuning API server resources <sky-api-server-resources-tuning>` for more details.

Default: ``false``

.. code-block:: yaml

  apiService:
    skipResourceCheck: false

.. _helm-values-apiService-resources:

``apiService.resources``
^^^^^^^^^^^^^^^^^^^^^^^^

Resource requests and limits for the API server container. Refer to :ref:`tuning API server resources <sky-api-server-resources-tuning>` for how to tune the resources.

Default: see the yaml below.

.. code-block:: yaml

  apiService:
    resources:
      requests:
        cpu: "4"
        memory: "8Gi"
      limits:
        cpu: "4"
        memory: "8Gi"

.. _helm-values-apiService-skypilotDev:

``apiService.skypilotDev``
^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable developer mode for SkyPilot.

Default: ``false``

.. code-block:: yaml

  apiService:
    skypilotDev: false

.. _helm-values-apiService-metrics:

``apiService.metrics``
^^^^^^^^^^^^^^^^^^^^^^

Configuration for metrics collection on the API server.

Default: see the yaml below.

.. code-block:: yaml

  apiService:
    metrics:
      enabled: true
      port: 9090

.. _helm-values-apiService-metrics-enabled:

``apiService.metrics.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable (exposing API metrics)[Link to docs/source/reference/api-server/examples/api-server-metrics-setup.rst] from the API server. If this is enabled and the API server image does not support metrics, the deployment will fail.

Default: ``false``

.. code-block:: yaml

  apiService:
    metrics:
      enabled: true

.. _helm-values-apiService-metrics-port:

``apiService.metrics.port``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The port to expose the metrics on.

Default: ``9090``

.. code-block:: yaml

  apiService:
    metrics:
      port: 9090

.. _helm-values-apiService-terminationGracePeriodSeconds:

``apiService.terminationGracePeriodSeconds``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The number of seconds to wait for the API server to finish processing the request before shutting down. Refer to :ref:`sky-api-server-graceful-upgrade` for more details.

Default: ``60``

.. code-block:: yaml

  apiService:
    terminationGracePeriodSeconds: 300

.. _helm-values-apiService-annotations:

``apiService.annotations``
^^^^^^^^^^^^^^^^^^^^^^^^^^

Custom annotations for the API server deployment.

Default: ``null``

.. code-block:: yaml

  apiService:
    annotations:
      my-annotation: "my-value"

.. _helm-values-apiService-extraEnvs:

``apiService.extraEnvs``
^^^^^^^^^^^^^^^^^^^^^^^^

Extra environment variables to set before starting the API server.

Default: ``null``

.. code-block:: yaml

  apiService:
    extraEnvs:
      - name: MY_ADDITIONAL_ENV_VAR
        value: "my_value"

.. _helm-values-apiService-extraVolumes:

``apiService.extraVolumes``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Extra volumes to mount to the API server.

Default: ``null``

.. code-block:: yaml

  apiService:
    extraVolumes:
      - name: my-volume
        secret:
          secretName: my-secret

.. _helm-values-apiService-extraVolumeMounts:

``apiService.extraVolumeMounts``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Extra volume mounts to mount to the API server.

Default: ``null``

.. code-block:: yaml

  apiService:
    extraVolumeMounts:
      - name: my-volume
        mountPath: /my-path
        subPath: my-file

.. _helm-values-apiService-sidecarContainers:

``apiService.sidecarContainers``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Additional containers to run alongside the API server.

Default: ``null``

.. code-block:: yaml

  apiService:
    sidecarContainers:
      - name: my-sidecar
        image: busybox
        args:
          - sleep
          - "3600"

.. _helm-values-apiService-logs:

``apiService.logs``
^^^^^^^^^^^^^^^^^^^

Configuration for managing API server logs.

Default: see the yaml below.

.. code-block:: yaml

  apiService:
    logs:
      retention:
        enabled: false
        size: 10M

.. _helm-values-apiService-logs-retention:

``apiService.logs.retention``
'''''''''''''''''''''''''''''

Configuration for log retention settings.

.. _helm-values-apiService-logs-retention-enabled:

``apiService.logs.retention.enabled``
''''''''''''''''''''''''''''''''''''''

Whether to enable log retention for the API server. When enabled, logs will be automatically rotated and managed according to the specified size limit.

Default: ``false``

.. code-block:: yaml

  apiService:
    logs:
      retention:
        enabled: true

.. _helm-values-apiService-logs-retention-size:

``apiService.logs.retention.size``
'''''''''''''''''''''''''''''''''''

The maximum size of the log file before rotation. When the log file reaches this size, it will be rotated to preserve disk space. Only used when ``enabled`` is ``true``.

Default: ``10M``

.. code-block:: yaml

  apiService:
    logs:
      retention:
        size: 50M


.. _helm-values-auth:

``auth``
~~~~~~~~

:ref:`Authentication configuration <api-server-auth>` for the API server.


.. _helm-values-auth-oauth:

``auth.oauth``
^^^^^^^^^^^^^^

OAuth2 Proxy based authentication configuration for the API server.

Default: see the yaml below.

.. code-block:: yaml

  auth:
    oauth:
      enabled: false
      oidc-issuer-url: null
      client-id: ""
      client-secret: ""
      client-details-from-secret: ""
      email-domain: "*"
      session-store-type: "redis"
      redis-url: null
      cookie-refresh: null
      cookie-expire: null

.. _helm-values-auth-oauth-enabled:

``auth.oauth.enabled``
''''''''''''''''''''''

Enable/disable OAuth2 Proxy based authentication on the API server. This is mutually exclusive with authentications on ingress, including :ref:`basic auth <helm-values-ingress-authCredentials>` and :ref:`OAuth2 Proxy on ingress <helm-values-ingress-oauth2-proxy>`.

Default: ``false``

.. code-block:: yaml

  auth:
    oauth:
      enabled: true

.. _helm-values-auth-oauth-oidc-issuer-url:

``auth.oauth.oidc-issuer-url``
''''''''''''''''''''''''''''''

The URL of the OIDC issuer (e.g., your Okta domain). Required when oauth is enabled.

Default: ``null``

.. code-block:: yaml

  auth:
    oauth:
      oidc-issuer-url: "https://mycompany.okta.com"

.. _helm-values-auth-oauth-client-id:

``auth.oauth.client-id``
''''''''''''''''''''''''

The OAuth client ID from your OIDC provider (e.g., Okta). Required when oauth is enabled.

Default: ``""``

.. code-block:: yaml

  auth:
    oauth:
      client-id: "0abc123def456"

.. _helm-values-auth-oauth-client-secret:

``auth.oauth.client-secret``
''''''''''''''''''''''''''''

The OAuth client secret from your OIDC provider (e.g., Okta). Required when oauth is enabled.

Default: ``""``

.. code-block:: yaml

  auth:
    oauth:
      client-secret: "abcdef123456"

.. _helm-values-auth-oauth-client-details-from-secret:

``auth.oauth.client-details-from-secret``
''''''''''''''''''''''''''''''''''''''''''

Alternative way to get both client ID and client secret from a Kubernetes secret. If set to a secret name, both ``client-id`` and ``client-secret`` values above are ignored. The secret must contain keys named either ``client-id`` and ``client-secret`` OR ``client_id`` and ``client_secret``. Both dash and underscore formats are supported for compatibility with different secret managers (e.g., HashiCorp Vault requires underscore format due to key naming constraints).

Default: ``""``

.. code-block:: yaml

  auth:
    oauth:
      client-details-from-secret: "oauth-client-credentials"

.. _helm-values-auth-oauth-email-domain:

``auth.oauth.email-domain``
'''''''''''''''''''''''''''

Email domains to allow for authentication. Use ``"*"`` to allow all email domains.

Default: ``"*"``

.. code-block:: yaml

  auth:
    oauth:
      email-domain: "mycompany.com"

.. _helm-values-auth-oauth-session-store-type:

``auth.oauth.session-store-type``
'''''''''''''''''''''''''''''''''

Session storage type for OAuth2 Proxy. Can be set to ``"cookie"`` or ``"redis"``. Using Redis as a session store results in smaller cookies and better performance for large-scale deployments.

Default: ``"redis"``

.. code-block:: yaml

  auth:
    oauth:
      session-store-type: "redis"

.. _helm-values-auth-oauth-redis-url:

``auth.oauth.redis-url``
''''''''''''''''''''''''

URL to connect to an external Redis instance for session storage. If set to ``null`` and ``session-store-type`` is ``"redis"``, a Redis instance will be automatically deployed. Format: ``redis://host[:port][/db-number]``

Default: ``null``

.. code-block:: yaml

  auth:
    oauth:
      redis-url: "redis://redis-host:6379/0"


.. _helm-values-auth-oauth-redis-secret:

``auth.oauth.redis-secret``
''''''''''''''''''''''''''''

Alternative way to specify Redis connection URL using a Kubernetes secret. The secret must contain a key named ``redis_url`` with the Redis connection URL in the format ``redis://host[:port][/db-number]``.

This field is mutually exclusive with :ref:`redis-url <helm-values-auth-oauth-redis-url>`.

Default: ``null``

.. code-block:: yaml

  auth:
    oauth:
      redis-secret: "my-redis-credentials"

.. _helm-values-auth-oauth-cookie-refresh:

``auth.oauth.cookie-refresh``
'''''''''''''''''''''''''''''

Duration in seconds after which to refresh the access token. This should typically be set to the access token lifespan minus 1 minute. If not set, tokens will not be refreshed automatically.

Default: ``null``

.. code-block:: yaml

  auth:
    oauth:
      cookie-refresh: 3540  # 59 minutes (for a 60-minute access token)

.. _helm-values-auth-oauth-cookie-expire:

``auth.oauth.cookie-expire``
''''''''''''''''''''''''''''

Expiration time for cookies in seconds. Should match the refresh token lifespan from your OIDC provider.

Default: ``null``

.. code-block:: yaml

  auth:
    oauth:
      cookie-expire: 86400  # 24 hours

.. _helm-values-auth-serviceAccount:

``auth.serviceAccount``
^^^^^^^^^^^^^^^^^^^^^^^

Service account token based authentication configuration for the API server.

.. code-block:: yaml

  auth:
    serviceAccount:
      enabled: null

.. _helm-values-auth-serviceAccount-enabled:

``auth.serviceAccount.enabled``
'''''''''''''''''''''''''''''''

Enable service account tokens for automated API access. If enabled, users can create bearer tokens to bypass SSO authentication for automated systems.

JWT secrets are automatically stored in the database for persistence across restarts. This setting defaults to the value of :ref:`.apiService.enableServiceAccounts <helm-values-apiService-enableServiceAccounts>` (which is ``true`` by default) for backward compatibility. Setting this field will override the default value.

Default: ``null``

.. code-block:: yaml

  auth:
    serviceAccount:
      enabled: true

.. _helm-values-auth-externalProxy:

``auth.externalProxy``
^^^^^^^^^^^^^^^^^^^^^^

Configuration for trusting an external authentication proxy in front of the API server. Use this when your infrastructure has a reverse proxy or load balancer that handles authentication (e.g., AWS ALB with Cognito, Azure Front Door with Azure AD, or a custom ingress controller with authentication middleware).

When enabled, the API server extracts user identity from the HTTP header set by the proxy. The proxy is trusted to have already authenticated the user.

This is mutually exclusive with :ref:`auth.oauth <helm-values-auth-oauth>` and :ref:`ingress.oauth2-proxy <helm-values-ingress-oauth2-proxy>`.

Default: see the yaml below.

.. code-block:: yaml

  auth:
    externalProxy:
      enabled: false
      headerName: 'X-Auth-Request-Email'
      headerFormat: 'plaintext'

.. _helm-values-auth-externalProxy-enabled:

``auth.externalProxy.enabled``
''''''''''''''''''''''''''''''

Enable external proxy authentication. When enabled, the API server will extract user identity from the header specified by ``headerName``.

Default: ``false``

.. code-block:: yaml

  auth:
    externalProxy:
      enabled: true

.. _helm-values-auth-externalProxy-headerName:

``auth.externalProxy.headerName``
'''''''''''''''''''''''''''''''''

The HTTP header name containing the user identity.

Default: ``'X-Auth-Request-Email'``

.. code-block:: yaml

  auth:
    externalProxy:
      headerName: 'X-WEBAUTH-USER'

.. _helm-values-auth-externalProxy-headerFormat:

``auth.externalProxy.headerFormat``
'''''''''''''''''''''''''''''''''''

The format of the header value. Available options:

- ``plaintext``: The header value is the user identity directly (e.g., ``user@example.com``)
- ``jwt``: The header value is a JWT token from which the identity should be extracted using ``jwtIdentityClaim``

Use ``jwt`` when integrating with load balancers that pass JWT tokens.

Default: ``'plaintext'``

.. code-block:: yaml

  auth:
    externalProxy:
      headerFormat: 'jwt'

.. _helm-values-auth-externalProxy-jwtIdentityClaim:

``auth.externalProxy.jwtIdentityClaim``
'''''''''''''''''''''''''''''''''''''''

The JWT claim to extract the user identity from when ``headerFormat`` is ``jwt``.

Only used when ``headerFormat`` is ``jwt``.

Default: ``'sub'``

.. code-block:: yaml

  auth:
    externalProxy:
      headerFormat: 'jwt'
      jwtIdentityClaim: 'email'


.. _helm-values-storage:

``storage``
~~~~~~~~~~~

.. _helm-values-storage-enabled:

``storage.enabled``
^^^^^^^^^^^^^^^^^^^

Enable persistent storage for the API server, setting this to ``false`` is prone to data loss and should only be used for testing.

When enabled, SkyPilot creates a PersistentVolumeClaim (PVC) to persist:

- **Managed job logs**: Accessible via ``sky jobs logs <job_id>`` and ``sky jobs logs --controller <job_id>``
- **File mounts**: Local files uploaded during managed job submission

.. note::

  Setting ``storage.enabled=true`` sets the environment variable ``SKYPILOT_API_SERVER_STORAGE_ENABLED=true`` on the API server pod. This ensures that managed job logs and file mounts persist across API server restarts and rolling updates.

  Transient logs (api_server logs, sky-* cluster logs) are NOT persisted to minimize storage usage.

For RollingUpdate upgrade strategy, see :ref:`apiService.upgradeStrategy <helm-values-apiService-upgradeStrategy>` for storage access mode requirements.

Default: ``true``

.. code-block:: yaml

  storage:
    enabled: true

.. _helm-values-storage-storageClassName:

``storage.storageClassName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Storage class to use for the API server, leave empty to use the default storage class of the hosting Kubernetes cluster.

Default: ``""``

.. code-block:: yaml

  storage:
    storageClassName: gp2

.. _helm-values-storage-accessMode:

``storage.accessMode``
^^^^^^^^^^^^^^^^^^^^^^

Access mode for the persistent storage volume. Available options:

- ``ReadWriteOnce`` (RWO): The volume can be mounted as read-write by a single node. This is the default and works with most storage classes. Compatible with ``Recreate`` upgrade strategy. **Not compatible with RollingUpdate upgrade strategy** since the PVC cannot be mounted by both old and new pods simultaneously during rolling updates.

- ``ReadWriteMany`` (RWX): The volume can be mounted as read-write by multiple nodes. Compatible with both ``Recreate`` and ``RollingUpdate`` upgrade strategies. Requires an RWX-capable storage class such as:

  - GKE: Filestore-backed storage class
  - EKS: EFS CSI driver
  - AKS: Azure Files
  - On-prem: NFS provisioner

For more details on upgrade strategies, see :ref:`apiService.upgradeStrategy <helm-values-apiService-upgradeStrategy>`.

Default: ``ReadWriteOnce``

.. code-block:: yaml

  # For Recreate upgrade strategy (default), ReadWriteOnce is sufficient
  storage:
    accessMode: ReadWriteOnce

  # For RollingUpdate upgrade strategy with persistent storage, use ReadWriteMany
  storage:
    accessMode: ReadWriteMany
    storageClassName: <your-rwx-storage-class>

.. _helm-values-storage-size:

``storage.size``
^^^^^^^^^^^^^^^^

Size of the persistent storage volume for the API server.

Default: ``10Gi``

.. code-block:: yaml

  storage:
    size: 10Gi

.. _helm-values-storage-selector:

``storage.selector``
^^^^^^^^^^^^^^^^^^^^

Selector for matching specific PersistentVolumes. Usually left empty.

Default: ``{}``

.. code-block:: yaml

  storage:
    selector: {}

.. _helm-values-storage-volumeName:

``storage.volumeName``
^^^^^^^^^^^^^^^^^^^^^^

Name of the PersistentVolume to bind to. Usually left empty to let Kubernetes select and bind the volume automatically.

Default: ``""``

.. code-block:: yaml

  storage:
    volumeName: ""

.. _helm-values-storage-annotations:

``storage.annotations``
^^^^^^^^^^^^^^^^^^^^^^^

Annotations to add to the PersistentVolumeClaim.

Default: ``{}``

.. code-block:: yaml

  storage:
    annotations: {}

.. _helm-values-ingress:

``ingress``
~~~~~~~~~~~

.. _helm-values-ingress-enabled:

``ingress.enabled``
^^^^^^^^^^^^^^^^^^^

Enable ingress for the API server. Set to ``true`` to expose the API server via an ingress controller.

Default: ``true``

.. code-block:: yaml

  ingress:
    enabled: true

.. _helm-values-ingress-unified:

``ingress.unified``
^^^^^^^^^^^^^^^^^^^

Use a single ingress resource for the API server and other auxiliary services. Dedicated ingresses for these services will be skipped, e.g. grafana and oauth2-proxy.

Default: ``false``

.. code-block:: yaml

  ingress:
    unified: false

.. _helm-values-ingress-authSecret:

``ingress.authSecret``
^^^^^^^^^^^^^^^^^^^^^^

Name of the Kubernetes secret containing basic auth credentials for ingress. If not specified, a new secret will be created using ``authCredentials``. This is ignored if ``ingress.oauth2-proxy.enabled`` is ``true``.

One of ``ingress.authSecret`` or ``ingress.authCredentials`` must be set, unless ``ingress.oauth2-proxy.enabled`` is ``true``.

Default: ``null``

.. code-block:: yaml

  ingress:
    authSecret: null

.. _helm-values-ingress-authCredentials:

``ingress.authCredentials``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Basic auth credentials in the format ``username:encrypted_password``. Used only if ``authSecret`` is not set. This is ignored if ``ingress.oauth2-proxy.enabled`` is ``true``.

One of ``ingress.authSecret`` or ``ingress.authCredentials`` must be set, unless ``ingress.oauth2-proxy.enabled`` is ``true``.

Default: ``null``

.. code-block:: yaml

  ingress:
    authCredentials: "username:$apr1$encrypted_password"

.. _helm-values-ingress-path:

``ingress.path``
^^^^^^^^^^^^^^^^

The base path of the API server. You may use different paths to expose multiple API servers through a unified ingress controller.

Default: ``'/'``

.. code-block:: yaml

  ingress:
    path: '/'

.. _helm-values-ingress-host:

``ingress.host``
^^^^^^^^^^^^^^^^

Host to exclusively accept traffic from (optional). Will respond to all host requests if not set.

Default: ``null``

.. code-block:: yaml

  ingress:
    host: api.mycompany.com

.. _helm-values-ingress-ingressClassName:

``ingress.ingressClassName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Ingress class name for newer Kubernetes versions.

Default: ``nginx``

.. code-block:: yaml

  ingress:
    ingressClassName: nginx

.. _helm-values-ingress-annotations:

``ingress.annotations``
^^^^^^^^^^^^^^^^^^^^^^^

Custom annotations for the ingress controller.

Default: ``null``

.. code-block:: yaml

  ingress:
    annotations:
      my-annotation: "my-value"

.. _helm-values-ingress-oauth2-proxy:

``ingress.oauth2-proxy``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Configuration for the OAuth2 Proxy authentication for the API server.

Deprecated: use :ref:`auth.oauth <helm-values-auth-oauth>` instead.

Default: see the yaml below.

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      enabled: false
      # Required when enabled:
      oidc-issuer-url: null
      client-id: ""
      client-secret: ""
      client-details-from-secret: ""
      # Optional settings:
      image: "quay.io/oauth2-proxy/oauth2-proxy:v7.9.0"
      use-https: false
      email-domain: "*"
      session-store-type: "redis"
      redis-url: null
      cookie-refresh: null
      cookie-expire: null

.. _helm-values-ingress-oauth2-proxy-enabled:

``ingress.oauth2-proxy.enabled``
''''''''''''''''''''''''''''''''''''

Enable OAuth2 Proxy for authentication. When enabled, this will deploy an OAuth2 Proxy component and configure the ingress to use it for authentication instead of basic auth.

Default: ``false``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      enabled: true

.. _helm-values-ingress-oauth2-proxy-oidc-issuer-url:

``ingress.oauth2-proxy.oidc-issuer-url``
''''''''''''''''''''''''''''''''''''''''

The URL of the OIDC issuer (e.g., your Okta domain). Required when oauth2-proxy is enabled.

Default: ``null``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      oidc-issuer-url: "https://mycompany.okta.com"

.. _helm-values-ingress-oauth2-proxy-client-id:

``ingress.oauth2-proxy.client-id``
''''''''''''''''''''''''''''''''''

The OAuth client ID from your OIDC provider (e.g., Okta). Required when oauth2-proxy is enabled.

Default: ``""``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      client-id: "0abc123def456"

.. _helm-values-ingress-oauth2-proxy-client-secret:

``ingress.oauth2-proxy.client-secret``
'''''''''''''''''''''''''''''''''''''''''

The OAuth client secret from your OIDC provider (e.g., Okta). Required when oauth2-proxy is enabled.

Default: ``""``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      client-secret: "abcdef123456"

.. _helm-values-ingress-oauth2-proxy-client-details-from-secret:

``ingress.oauth2-proxy.client-details-from-secret``
'''''''''''''''''''''''''''''''''''''''''''''''''''

Alternative way to get both client ID and client secret from a Kubernetes secret. If set to a secret name, both ``client-id`` and ``client-secret`` values above are ignored. The secret must contain keys named ``client-id`` and ``client-secret``.

Default: ``""``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      client-details-from-secret: "oauth-client-credentials"

.. _helm-values-ingress-oauth2-proxy-image:

``ingress.oauth2-proxy.image``
''''''''''''''''''''''''''''''

Docker image for the OAuth2 Proxy component.

Default: ``"quay.io/oauth2-proxy/oauth2-proxy:v7.9.0"``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      image: "quay.io/oauth2-proxy/oauth2-proxy:v7.9.0"

.. _helm-values-ingress-oauth2-proxy-use-https:

``ingress.oauth2-proxy.use-https``
''''''''''''''''''''''''''''''''''

Set to ``true`` when using HTTPS for the API server endpoint. When set to ``false``, secure cookies are disabled, which is required for HTTP endpoints.

Default: ``false``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      use-https: true

.. _helm-values-ingress-oauth2-proxy-email-domain:

``ingress.oauth2-proxy.email-domain``
'''''''''''''''''''''''''''''''''''''''

Email domains to allow for authentication. Use ``"*"`` to allow all email domains.

Default: ``"*"``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      email-domain: "mycompany.com"

.. _helm-values-ingress-oauth2-proxy-session-store-type:

``ingress.oauth2-proxy.session-store-type``
'''''''''''''''''''''''''''''''''''''''''''

Session storage type for OAuth2 Proxy. Can be set to ``"cookie"`` or ``"redis"``. Using Redis as a session store results in smaller cookies and better performance for large-scale deployments.

Default: ``"redis"``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      session-store-type: "redis"

.. _helm-values-ingress-oauth2-proxy-redis-url:

``ingress.oauth2-proxy.redis-url``
''''''''''''''''''''''''''''''''''

URL to connect to an external Redis instance for session storage. If set to ``null`` and ``session-store-type`` is ``"redis"``, a Redis instance will be automatically deployed. Format: ``redis://host[:port][/db-number]``

Default: ``null``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      redis-url: "redis://redis-host:6379/0"

.. _helm-values-ingress-oauth2-proxy-cookie-refresh:

``ingress.oauth2-proxy.cookie-refresh``
'''''''''''''''''''''''''''''''''''''''

Duration in seconds after which to refresh the access token. This should typically be set to the access token lifespan minus 1 minute. If not set, tokens will not be refreshed automatically.

Default: ``null``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      cookie-refresh: 3540  # 59 minutes (for a 60-minute access token)

.. _helm-values-ingress-oauth2-proxy-cookie-expire:

``ingress.oauth2-proxy.cookie-expire``
''''''''''''''''''''''''''''''''''''''

Expiration time for cookies in seconds. Should match the refresh token lifespan from your OIDC provider.

Default: ``null``

.. code-block:: yaml

  ingress:
    oauth2-proxy:
      cookie-expire: 86400  # 24 hours

.. _helm-values-ingress-tls:

``ingress.tls``
^^^^^^^^^^^^^^^
TLS configuration for the ingress controller. When setting to ``true``, TLS will be enabled. User can either provide their own TLS secret with a name ``<release-name>-tls-secrets`` or use ``cert-manager`` to automatically manage TLS certificates.

Default: ``false``

Example with TLS enabled using `cert-manager <https://cert-manager.io/docs/>`_:

.. code-block:: yaml

  ingress:
    enabled: true
    host: skypilot.example.com
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt
      kubernetes.io/ingress.allow-http: "false"
    tls:
      enabled: true

.. _helm-values-ingress-tls-enabled:

``ingress.tls.enabled``
'''''''''''''''''''''''

Enable TLS for the ingress. When enabled, either cert-manager annotation should be provided or a TLS secret with name ``<release-name>-tls-secrets`` should be created in the namespace.

.. _helm-values-ingress-nginx:

``ingress-nginx``
~~~~~~~~~~~~~~~~~

.. _helm-values-ingress-nginx-enabled:

``ingress-nginx.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^

Enable the ingress-nginx controller for the API server. If you have an existing ingress-nginx controller, you have to set this to ``false`` to avoid conflict.

Default: ``true``

.. code-block:: yaml

  ingress-nginx:
    enabled: true

.. _helm-values-ingress-nginx-controller:

``ingress-nginx.controller``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Fields under ``ingress-nginx.controller`` will be mapped to ``controller`` values for the ingress-nginx controller sub-chart. Refer to the `ingress-nginx chart documentation <https://artifacthub.io/packages/helm/ingress-nginx/ingress-nginx#values>`_ for more details.

Default: see the yaml below.

.. code-block:: yaml

  ingress-nginx:
    controller:
      service:
        # Service type of the ingress controller.
        type: LoadBalancer
        # Annotations for the ingress controller service.
        annotations:
          service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
          cloud.google.com/l4-rbs: "enabled"
          service.beta.kubernetes.io/port_443_health-probe_protocol: "TCP"
          service.beta.kubernetes.io/port_80_health-probe_protocol: "TCP"
      config:
        # Enable gzip compression for API responses
        use-gzip: "true"
        gzip-level: 5
        gzip-min-length: 1000
        # Custom HTTP snippet to inject into the ingress-nginx configuration.
        http-snippet: |
          map $http_upgrade $connection_upgrade {
              default upgrade;
              ''      close;
          }

.. _helm-values-rbac:

``rbac``
~~~~~~~~

.. _helm-values-rbac-create:

``rbac.create``
^^^^^^^^^^^^^^^

Whether to create the service account and RBAC policies for the API server. If false, an external service account is expected.

Default: ``true``

.. code-block:: yaml

  rbac:
    create: true

.. _helm-values-rbac-serviceAccountName:

``rbac.serviceAccountName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the service account to use. Leave empty to let the chart generate one.

Default: ``""``

.. code-block:: yaml

  rbac:
    serviceAccountName: ""

.. _helm-values-rbac-namespaceRules:

``rbac.namespaceRules``
^^^^^^^^^^^^^^^^^^^^^^^

Namespace-scoped RBAC rules granted to the namespace where the SkyPilot tasks will be launched.

.. note::

  Modifying the rules may break functionalities of SkyPilot API server. Refer to :ref:`setting minimum permissions in helm deployment <minimum-permissions-in-helm>` for how to modify the rules based on your use case.

Default: see the yaml below.

.. code-block:: yaml

  rbac:
    namespaceRules:
      - apiGroups: [ "" ]
        resources: [ "pods", "pods/status", "pods/exec", "pods/portforward" ]
        verbs: [ "*" ]
      - apiGroups: [ "" ]
        resources: [ "services" ]
        verbs: [ "*" ]
      - apiGroups: [ "" ]
        resources: [ "secrets" ]
        verbs: [ "*" ]
      - apiGroups: [ "" ]
        resources: [ "events" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "" ]
        resources: [ "configmaps" ]
        verbs: [ "get", "patch" ]
      - apiGroups: ["apps"]
        resources: ["deployments", "deployments/status"]
        verbs: ["*"]
      - apiGroups: [ "" ]
        resources: [ "configmaps" ]
        verbs: [ "get", "patch" ]
      - apiGroups: ["apps"]
        resources: ["deployments", "deployments/status"]
        verbs: ["*"]
      - apiGroups: [""]
        resources: ["persistentvolumeclaims"]
        verbs: ["*"]

.. _helm-values-rbac-clusterRules:

``rbac.clusterRules``
^^^^^^^^^^^^^^^^^^^^^^

Cluster-scoped RBAC rules for the API server.

.. note::

  Modifying the rules may break functionalities of SkyPilot API server. Refer to :ref:`setting minimum permissions in helm deployment <minimum-permissions-in-helm>` for how to modify the rules based on your use case.

Default: see the yaml below.

.. code-block:: yaml

  rbac:
    clusterRules:
      - apiGroups: [ "" ]
        resources: [ "nodes" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "" ]
        resources: [ "pods" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "node.k8s.io" ]
        resources: [ "runtimeclasses" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [ "networking.k8s.io" ]
        resources: [ "ingressclasses" ]
        verbs: [ "get", "list", "watch" ]
      - apiGroups: [""]
        resources: ["services"]
        verbs: ["list", "get"]

.. _helm-values-rbac-manageRbacPolicies:

``rbac.manageRbacPolicies``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Allow the API server to grant permissions to SkyPilot Pods and system components. Refer to :ref:`setting minimum permissions in helm deployment <minimum-permissions-in-helm>` for more details.

Default: ``true``

.. code-block:: yaml

  rbac:
    manageRbacPolicies: true

.. _helm-values-rbac-manageSystemComponents:

``rbac.manageSystemComponents``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Allow the API server to manage system components in the skypilot-system namespace. Required for object store mounting.

Default: ``true``

.. code-block:: yaml

  rbac:
    manageSystemComponents: true

.. _helm-values-rbac-serviceAccountAnnotations:

``rbac.serviceAccountAnnotations``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Custom annotations for the API server service account. This is useful for cloud provider integrations that require specific annotations on service accounts, such as AWS IAM roles for service accounts (IRSA) or GCP Workload Identity.

Default: ``null``

.. code-block:: yaml

  rbac:
    serviceAccountAnnotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::123456789012:role/MyServiceAccountRole"
      iam.gke.io/gcp-service-account: "my-gcp-service-account@my-project.iam.gserviceaccount.com"

.. _helm-values-kubernetesCredentials:

``kubernetesCredentials``
~~~~~~~~~~~~~~~~~~~~~~~~~

.. _helm-values-kubernetesCredentials-useApiServerCluster:

``kubernetesCredentials.useApiServerCluster``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable using the API server's cluster for workloads.

Default: ``true``

.. code-block:: yaml

  kubernetesCredentials:
    useApiServerCluster: true

.. _helm-values-kubernetesCredentials-useKubeconfig:

``kubernetesCredentials.useKubeconfig``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use the `kube-credentials` secret containing the kubeconfig to authenticate to Kubernetes.

Default: ``false``

.. code-block:: yaml

  kubernetesCredentials:
    useKubeconfig: false

.. _helm-values-kubernetesCredentials-kubeconfigSecretName:

``kubernetesCredentials.kubeconfigSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the kubeconfig file. Only used if useKubeconfig is true.

Default: ``kube-credentials``

.. code-block:: yaml

  kubernetesCredentials:
    kubeconfigSecretName: kube-credentials

.. _helm-values-kubernetesCredentials-inclusterNamespace:

``kubernetesCredentials.inclusterNamespace``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Namespace to use for in-cluster resources.

Default: ``null``

.. code-block:: yaml

  kubernetesCredentials:
    inclusterNamespace: null

.. _helm-values-awsCredentials:

``awsCredentials``
~~~~~~~~~~~~~~~~~~

.. _helm-values-awsCredentials-enabled:

``awsCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable AWS credentials for the API server.

Default: ``false``

.. code-block:: yaml

  awsCredentials:
    enabled: false

.. _helm-values-awsCredentials-awsSecretName:

``awsCredentials.awsSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the AWS credentials. Only used if enabled is true.

Default: ``aws-credentials``

.. code-block:: yaml

  awsCredentials:
    awsSecretName: aws-credentials

.. _helm-values-awsCredentials-useCredentialsFile:

``awsCredentials.useCredentialsFile``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Set to ``true`` to mount a complete AWS credentials file with multiple profiles. This is required for using different AWS profiles with different :ref:`workspaces <workspaces>`.

If set to ``true``, the secret must contain a key named ``credentials`` with the full AWS credentials file content. Create the secret with:

.. code-block:: bash

    kubectl create secret generic aws-credentials \
      --namespace $NAMESPACE \
      --from-file=credentials=$HOME/.aws/credentials

If set to ``false`` (default), ``accessKeyIdKeyName`` and ``secretAccessKeyKeyName`` are used as the default profile credentials.

Default: ``false``

.. code-block:: yaml

  awsCredentials:
    enabled: true
    useCredentialsFile: true


.. _helm-values-awsCredentials-accessKeyIdKeyName:

``awsCredentials.accessKeyIdKeyName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Key name used to set AWS_ACCESS_KEY_ID. Only used when ``useCredentialsFile`` is ``false``.

Default: ``aws_access_key_id``

.. code-block:: yaml

  awsCredentials:
    accessKeyIdKeyName: aws_access_key_id

.. _helm-values-awsCredentials-secretAccessKeyKeyName:

``awsCredentials.secretAccessKeyKeyName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Key name used to set AWS_SECRET_ACCESS_KEY. Only used when ``useCredentialsFile`` is ``false``.

Default: ``aws_secret_access_key``

.. code-block:: yaml

  awsCredentials:
    secretAccessKeyKeyName: aws_secret_access_key

.. _helm-values-gcpCredentials:

``gcpCredentials``
~~~~~~~~~~~~~~~~~~

.. _helm-values-gcpCredentials-enabled:

``gcpCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable GCP credentials for the API server.

Default: ``false``

.. code-block:: yaml

  gcpCredentials:
    enabled: false

.. _helm-values-gcpCredentials-projectId:

``gcpCredentials.projectId``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

GCP project ID. Only used if enabled is true.

Default: ``null``

.. code-block:: yaml

  gcpCredentials:
    projectId: null

.. _helm-values-gcpCredentials-gcpSecretName:

``gcpCredentials.gcpSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the GCP credentials. Only used if enabled is true.

Default: ``gcp-credentials``

.. code-block:: yaml

  gcpCredentials:
    gcpSecretName: gcp-credentials

.. _helm-values-r2Credentials:

``r2Credentials``
~~~~~~~~~~~~~~~~~

.. _helm-values-r2Credentials-enabled:

``r2Credentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable R2 credentials for the API server.

.. code-block:: yaml

  r2Credentials:
    enabled: true

.. _helm-values-r2Credentials-r2SecretName:

``r2Credentials.r2SecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the R2 credentials. Only used if enabled is true. The secret should contain the following keys:

- ``r2.credentials``: R2 credentials file
- ``accountid``: R2 account ID file

Refer to :ref:`Cloudflare R2 installation <cloudflare-r2-installation>` for more details.

Default: ``r2-credentials``

.. code-block:: yaml

  r2Credentials:
    r2SecretName: your-r2-credentials-secret-name

.. _helm-values-runpodCredentials:

``runpodCredentials``
~~~~~~~~~~~~~~~~~~~~~

.. _helm-values-runpodCredentials-enabled:

``runpodCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable RunPod credentials for the API server.

Default: ``false``

.. code-block:: yaml

  runpodCredentials:
    enabled: false

.. _helm-values-runpodCredentials-runpodSecretName:

``runpodCredentials.runpodSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the RunPod credentials. Only used if enabled is true.

Default: ``runpod-credentials``

.. code-block:: yaml

  runpodCredentials:
    runpodSecretName: runpod-credentials

.. _helm-values-lambdaCredentials:

``lambdaCredentials``
~~~~~~~~~~~~~~~~~~~~~

.. _helm-values-lambdaCredentials-enabled:

``lambdaCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable Lambda credentials for the API server.

Default: ``false``

.. code-block:: yaml

  lambdaCredentials:
    enabled: false

.. _helm-values-lambdaCredentials-lambdaSecretName:

``lambdaCredentials.lambdaSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the Lambda credentials. Only used if enabled is true.

Default: ``lambda-credentials``

.. code-block:: yaml

  lambdaCredentials:
    lambdaSecretName: lambda-credentials

.. _helm-values-vastCredentials:

``vastCredentials``
~~~~~~~~~~~~~~~~~~~

.. _helm-values-vastCredentials-enabled:

``vastCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable Vast credentials for the API server.

Default: ``false``

.. code-block:: yaml

  vastCredentials:
    enabled: false

.. _helm-values-vastCredentials-vastSecretName:

``vastCredentials.vastSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the Vast credentials. Only used if enabled is true.

Default: ``vast-credentials``

.. code-block:: yaml

  vastCredentials:
    vastSecretName: vast-credentials

.. _helm-values-nebiusCredentials:

``nebiusCredentials``
~~~~~~~~~~~~~~~~~~~~~

.. _helm-values-nebiusCredentials-enabled:

``nebiusCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable Nebius credentials for the API server.

Default: ``false``

.. code-block:: yaml

  nebiusCredentials:
    enabled: false

.. _helm-values-nebiusCredentials-tenantId:

``nebiusCredentials.tenantId``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Nebius tenant ID. Only used if enabled is true.

Default: ``null``

.. code-block:: yaml

  nebiusCredentials:
    tenantId: null

.. _helm-values-nebiusCredentials-nebiusSecretName:

``nebiusCredentials.nebiusSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the Nebius credentials. Only used if enabled is true.

Default: ``nebius-credentials``

.. code-block:: yaml

  nebiusCredentials:
    nebiusSecretName: nebius-credentials

.. _helm-values-coreweaveCredentials:

``coreweaveCredentials``
~~~~~~~~~~~~~~~~~~~~~~~~

.. _helm-values-coreweaveCredentials-enabled:

``coreweaveCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable CoreWeave CAIOS credentials for the API server.

Default: ``false``

.. code-block:: yaml

  coreweaveCredentials:
    enabled: false

.. _helm-values-coreweaveCredentials-coreweaveSecretName:

``coreweaveCredentials.coreweaveSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the CoreWeave CAIOS credentials. Only used if enabled is true. The secret should contain the following keys:

- ``cw.config``: CoreWeave CAIOS config file
- ``cw.credentials``: CoreWeave CAIOS credentials file

Refer to :ref:`CoreWeave CAIOS installation <coreweave-caios-installation>` for more details.

Default: ``coreweave-credentials``

.. code-block:: yaml

  coreweaveCredentials:
    coreweaveSecretName: coreweave-credentials

.. _helm-values-digitaloceanCredentials:

``digitaloceanCredentials``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. _helm-values-digitaloceanCredentials-enabled:

``digitaloceanCredentials.enabled``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Enable DigitalOcean credentials for the API server.

Default: ``false``

.. code-block:: yaml

  digitaloceanCredentials:
    enabled: false

.. _helm-values-digitaloceanCredentials-digitaloceanSecretName:

``digitaloceanCredentials.digitaloceanSecretName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Name of the secret containing the DigitalOcean credentials. Only used if enabled is true.

Default: ``digitalocean-credentials``

.. code-block:: yaml

  digitaloceanCredentials:
    digitaloceanSecretName: digitalocean-credentials

.. _helm-values-extraInitContainers:

``extraInitContainers``
~~~~~~~~~~~~~~~~~~~~~~~

Additional init containers to add to the API server pod.

Default: ``null``

.. code-block:: yaml

  extraInitContainers:
    - name: my-init-container
      image: my-image:latest
      command: ["/bin/sh", "-c", "echo 'Hello from init container'"]

.. _helm-values-podSecurityContext:

``podSecurityContext``
~~~~~~~~~~~~~~~~~~~~~~

Security context for the API server pod. Usually left empty to use defaults. Refer to `set the security context for Pod <https://kubernetes.io/docs/tasks/configure-pod-container/security-context/#set-the-security-context-for-a-pod>`_ for more details.

Default: ``{}``

.. code-block:: yaml

  podSecurityContext:
    runAsUser: 1000
    runAsGroup: 3000
    fsGroup: 2000

.. _helm-values-securityContext:

``securityContext``
~~~~~~~~~~~~~~~~~~~

.. _helm-values-securityContext-capabilities:

``securityContext.capabilities``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Linux capabilities to drop for the API server container.

Default: drop all capabilities.

.. code-block:: yaml

  securityContext:
    capabilities:
      drop:
      - ALL

.. _helm-values-securityContext-allowPrivilegeEscalation:

``securityContext.allowPrivilegeEscalation``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Whether to allow privilege escalation in the API server container.

Default: ``false``

.. code-block:: yaml

  securityContext:
    allowPrivilegeEscalation: false

.. _helm-values-runtimeClassName:

``runtimeClassName``
~~~~~~~~~~~~~~~~~~~~

The runtime class to use for the API server pod. Usually left empty to use the default runtime class.

Default: (empty)

.. code-block:: yaml

  runtimeClassName:

.. _helm-values-prometheus:

``prometheus``
~~~~~~~~~~~~~~

Configuration for Prometheus helm chart. Refer to the `Prometheus helm chart repository <https://github.com/prometheus-community/helm-charts/blob/main/charts/prometheus/values.yaml>`_ for available values.

SkyPilot provides a minimal Prometheus configuration by default. If you want to monitor more resources other than the API server, it is recommended to install and manage Prometheus separately.

.. code-block:: yaml

  prometheus:
    enabled: true
    server:
      persistentVolume:
        enabled: true
        size: 10Gi
    extraScrapeConfigs: |
      # Static scrape target for SkyPilot API server GPU metrics
      - job_name: 'skypilot-api-server-gpu-metrics'
        static_configs:
          - targets: ['{{ .Release.Name }}-api-service.{{ .Release.Namespace }}.svc.cluster.local:80']
        metrics_path: '/gpu-metrics'
        scrape_interval: 15s
        scrape_timeout: 10s
    kube-state-metrics:
      enabled: true
      metricLabelsAllowlist:
        - pods=[skypilot-cluster-name]
    prometheus-node-exporter:
      enabled: false
    prometheus-pushgateway:
      enabled: false
    alertmanager:
      enabled: false

.. _helm-values-prometheus-enabled:

``prometheus.enabled``
^^^^^^^^^^^^^^^^^^^^^^

Enable prometheus for the API server.

Default: ``false``

.. code-block:: yaml

  prometheus:
    enabled: false

.. _helm-values-grafana:

``grafana``
~~~~~~~~~~~~

Configuration for Grafana helm chart. Refer to the `Grafana helm chart documentation <https://github.com/grafana/helm-charts/blob/main/charts/grafana/README.md>`_ for available values.

By default, Grafana is configured to work with the ingress controller and auth proxy for seamless authentication.

.. code-block:: yaml

  grafana:
    enabled: true
    persistence:
      enabled: true
      size: 10Gi
    ingress:
      enabled: false
      enableAuthedIngress: true
      path: "/grafana"
      ingressClassName: nginx
      hosts: null
    grafana.ini:
      server:
        domain: localhost
        root_url: "%(protocol)s://%(domain)s/grafana"
        enforce_domain: false
        serve_from_sub_path: true
      security:
        allow_embedding: true
      auth.proxy:
        enabled: true
        header_name: "X-WEBAUTH-USER"
        header_property: "username"
        auto_sign_up: true
      auth:
        disable_login_form: true
        disable_signout_menu: true
      auth.anonymous:
        enabled: false
      auth.basic:
        enabled: false
    sidecar:
      datasources:
        enabled: true
        initDatasources: true
      dashboards:
        enabled: true
    dashboardProviders:
      dashboardproviders.yaml:
        apiVersion: 1
        providers:
        - name: 'default'
          orgId: 1
          folder: ''
          type: file
          disableDeletion: false
          allowUiUpdates: false
          updateIntervalSeconds: 30
          options:
            path: /var/lib/grafana/dashboards/default

.. _helm-values-grafana-enabled:

``grafana.enabled``
^^^^^^^^^^^^^^^^^^^^

Enable grafana for the API server.

Default: ``false``

.. code-block:: yaml

  grafana:
    enabled: false

.. _helm-values-spec:

SkyPilot API Server Helm Chart Values
=====================================

The SkyPilot API server helm chart provides typical `helm values <https://helm.sh/docs/chart_template_guide/values_files/>`_ as configuration entries. Configuration values can be passed in two ways when installing the chart:

* ``--values`` (or ``-f``): Specify a YAML file with overrides.

  .. code-block:: bash

      cat <<EOF > values.yaml
      apiService:
        image: berkeleyskypilot/skypilot:0.9.1
      EOF

      helm install $RELEASE_NAME skypilot/skypilot-nightly --devel --values values.yaml

* ``--set``: Specify overrides on the command line.

  .. code-block:: bash
      
      helm install $RELEASE_NAME skypilot/skypilot-nightly --set apiService.image="berkeleyskypilot/skypilot:0.9.1"

Values
------

Below is the available helm value keys and the default value of each key:

..
  Omitted values:
  * storage.accessMode: accessMode other than ReadWriteOnce is not tested yet.

.. parsed-literal::

  :ref:`apiService <helm-values-apiService>`:
    :ref:`image <helm-values-apiService-image>`: berkeleyskypilot/skypilot:0.9.1
    :ref:`preDeployHook <helm-values-apiService-preDeployHook>`: \|-
      # Run commands before deploying the API server, e.g. installing an admin
      # policy. Remember to set the admin policy in the config section below.

      echo "Pre-deploy hook"

      # Uncomment the following lines to install the admin policy

      # echo "Installing admin policy"
      # pip install git+https://github.com/michaelvll/admin-policy-examples
    :ref:`config <helm-values-apiService-config>`: null
    :ref:`skipResourceCheck <helm-values-apiService-skipResourceCheck>`: false
    :ref:`resources <helm-values-apiService-resources>`:
      requests:
        cpu: "4"
        memory: "8Gi"
      limits:
        cpu: "4"
        memory: "8Gi"
    :ref:`skypilotDev <helm-values-apiService-skypilotDev>`: false

  :ref:`storage <helm-values-storage>`:
    :ref:`enabled <helm-values-storage-enabled>`: true
    :ref:`storageClassName <helm-values-storage-storageClassName>`: ""
    :ref:`size <helm-values-storage-size>`: 10Gi
    :ref:`selector <helm-values-storage-selector>`: {}
    :ref:`volumeName <helm-values-storage-volumeName>`: ""
    :ref:`annotations <helm-values-storage-annotations>`: {}

  :ref:`ingress <helm-values-ingress>`:
    :ref:`enabled <helm-values-ingress-enabled>`: true
    :ref:`authSecret <helm-values-ingress-authSecret>`: null
    :ref:`authCredentials <helm-values-ingress-authCredentials>`: "username:$apr1$encrypted_password"
    :ref:`path <helm-values-ingress-path>`: '/'

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

  :ref:`kubernetesCredentials <helm-values-kubernetesCredentials>`:
    :ref:`useApiServerCluster <helm-values-kubernetesCredentials-useApiServerCluster>`: true
    :ref:`useKubeconfig <helm-values-kubernetesCredentials-useKubeconfig>`: false
    :ref:`kubeconfigSecretName <helm-values-kubernetesCredentials-kubeconfigSecretName>`: kube-credentials
    :ref:`inclusterNamespace <helm-values-kubernetesCredentials-inclusterNamespace>`: null

  :ref:`awsCredentials <helm-values-awsCredentials>`:
    :ref:`enabled <helm-values-awsCredentials-enabled>`: false
    :ref:`awsSecretName <helm-values-awsCredentials-awsSecretName>`: aws-credentials
    :ref:`accessKeyIdKeyName <helm-values-awsCredentials-accessKeyIdKeyName>`: aws_access_key_id
    :ref:`secretAccessKeyKeyName <helm-values-awsCredentials-secretAccessKeyKeyName>`: aws_secret_access_key

  :ref:`gcpCredentials <helm-values-gcpCredentials>`:
    :ref:`enabled <helm-values-gcpCredentials-enabled>`: false
    :ref:`projectId <helm-values-gcpCredentials-projectId>`: null
    :ref:`gcpSecretName <helm-values-gcpCredentials-gcpSecretName>`: gcp-credentials

  :ref:`podSecurityContext <helm-values-podSecurityContext>`: {}

  :ref:`securityContext <helm-values-securityContext>`:
    :ref:`capabilities <helm-values-securityContext-capabilities>`:
      drop:
      - ALL
    :ref:`allowPrivilegeEscalation <helm-values-securityContext-allowPrivilegeEscalation>`: false

  :ref:`runtimeClassName <helm-values-runtimeClassName>`: ""

Fields
----------

.. _helm-values-apiService:

``apiService``
~~~~~~~~~~~~~~

Configuration for the SkyPilot API server deployment.

.. _helm-values-apiService-image:

``apiService.image``
^^^^^^^^^^^^^^^^^^^^

Docker image to use for the API server.

Default: ``"berkeleyskypilot/skypilot:0.9.1"``

.. code-block:: yaml

  apiService:
    image: berkeleyskypilot/skypilot:0.9.1

To use a nightly build, find the desired nightly version on `pypi <https://pypi.org/project/skypilot-nightly/#history>`_ and update the ``image`` value:

.. code-block:: yaml

  apiService:
    # Replace 1.0.0.devYYYYMMDD with the desired nightly version
    image: berkeleyskypilot/skypilot-nightly:1.0.0.devYYYYMMDD

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

.. _helm-values-storage:

``storage``
~~~~~~~~~~~

.. _helm-values-storage-enabled:

``storage.enabled``
^^^^^^^^^^^^^^^^^^^

Enable persistent storage for the API server, setting this to ``false`` is prone to data loss and should only be used for testing.

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

.. _helm-values-ingress-authSecret:

``ingress.authSecret``
^^^^^^^^^^^^^^^^^^^^^^

Name of the Kubernetes secret containing basic auth credentials for ingress. If not specified, a new secret will be created using ``authCredentials``.

One of ``ingress.authSecret`` or ``ingress.authCredentials`` must be set.

Default: ``null``

.. code-block:: yaml

  ingress:
    authSecret: null

.. _helm-values-ingress-authCredentials:

``ingress.authCredentials``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Basic auth credentials in the format ``username:encrypted_password``. Used only if ``authSecret`` is not set.

One of ``ingress.authSecret`` or ``ingress.authCredentials`` must be set.

Default: ``"username:$apr1$encrypted_password"``

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
      - apiGroups: ["" ]
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

.. _helm-values-awsCredentials-accessKeyIdKeyName:

``awsCredentials.accessKeyIdKeyName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Key name used to set AWS_ACCESS_KEY_ID.

Default: ``aws_access_key_id``

.. code-block:: yaml

  awsCredentials:
    accessKeyIdKeyName: aws_access_key_id

.. _helm-values-awsCredentials-secretAccessKeyKeyName:

``awsCredentials.secretAccessKeyKeyName``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Key name used to set AWS_SECRET_ACCESS_KEY.

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

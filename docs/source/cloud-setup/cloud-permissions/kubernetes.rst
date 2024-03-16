.. _cloud-permissions-kubernetes:

Kubernetes
==========

SkyPilot requires permissions equivalent to the following roles to be able to manage the resources in the Kubernetes cluster:

.. code-block:: yaml

    # Namespaced role for the service account
    # Required for creating pods, services and other necessary resources in the namespace.
    # Note these permissions only apply in the namespace where SkyPilot is deployed.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
        name: sky-sa-role
        namespace: default
    rules:
      - apiGroups: ["*"]
        resources: ["*"]
        verbs: ["*"]
    ---
    # ClusterRole for accessing cluster-wide resources. Details for each resource below:
    kind: ClusterRole
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
        name: sky-sa-cluster-role
        namespace: default
        labels:
            parent: skypilot
    rules:
    - apiGroups: [""]
      resources: ["nodes"]  # Required for getting node resources.
      verbs: ["get", "list", "watch"]
    - apiGroups: ["rbac.authorization.k8s.io"]
      resources: ["clusterroles", "clusterrolebindings"]  # Required for launching more SkyPilot clusters from within the pod.
      verbs: ["get", "list", "watch"]
    - apiGroups: ["node.k8s.io"]
      resources: ["runtimeclasses"]   # Required for autodetecting the runtime class of the nodes.
      verbs: ["get", "list", "watch"]
    ---
    # If using ingresses, role for accessing ingress service IP
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      namespace: ingress-nginx
      name: sky-sa-role-ingress-nginx
    rules:
    - apiGroups: [""]
      resources: ["services"]
      verbs: ["list", "get"]

Example Service Account YAML
----------------------------

To create a service account bound with these roles, you can use the following YAML:

.. code-block:: yaml

    # create-sky-sa.yaml
    kind: ServiceAccount
    apiVersion: v1
    metadata:
      name: sky-sa
      namespace: default
      labels:
        parent: skypilot
    ---
    # Role for the service account
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-role
      namespace: default
      labels:
        parent: skypilot
    rules:
      - apiGroups: ["*"]  # Required for creating pods, services, secrets and other necessary resources in the namespace.
        resources: ["*"]
        verbs: ["*"]
    ---
    # RoleBinding for the service account
    kind: RoleBinding
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-rb
      namespace: default
      labels:
        parent: skypilot
    subjects:
    - kind: ServiceAccount
      name: sky-sa
    roleRef:
        kind: Role
        name: sky-sa-role
        apiGroup: rbac.authorization.k8s.io
    ---
    # Role for accessing ingress resources
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      namespace: ingress-nginx
      name: sky-sa-role-ingress-nginx
    rules:
    - apiGroups: [""]
      resources: ["services"]
      verbs: ["list", "get"]
    ---
    # RoleBinding for accessing ingress resources
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: sky-sa-rolebinding-ingress-nginx
      namespace: ingress-nginx
    subjects:
    - kind: ServiceAccount
      name: sky-sa
      namespace: default
    roleRef:
      kind: Role
      name: sky-sa-role-ingress-nginx
      apiGroup: rbac.authorization.k8s.io
    ---
    # ClusterRole for the service account
    kind: ClusterRole
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-cluster-role
      namespace: default
      labels:
        parent: skypilot
    rules:
    - apiGroups: [""]
      resources: ["nodes"]  # Required for getting node resources.
      verbs: ["get", "list", "watch"]
    - apiGroups: ["rbac.authorization.k8s.io"]
      resources: ["clusterroles", "clusterrolebindings"]  # Required for launching more SkyPilot clusters from within the pod.
      verbs: ["get", "list", "watch"]
    - apiGroups: ["node.k8s.io"]
      resources: ["runtimeclasses"]   # Required for autodetecting the runtime class of the nodes.
      verbs: ["get", "list", "watch"]
    - apiGroups: ["networking.k8s.io"]   # Required for exposing services.
      resources: ["ingressclasses"]
      verbs: ["get", "list", "watch"]
    ---
    # ClusterRoleBinding for the service account
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: sky-sa-cluster-role-binding
      namespace: default
      labels:
          parent: skypilot
    subjects:
    - kind: ServiceAccount
      name: sky-sa
      namespace: default
    roleRef:
        kind: ClusterRole
        name: sky-sa-cluster-role
        apiGroup: rbac.authorization.k8s.io

.. code-block:: bash

    kubectl apply -f create-sky-sa.yaml

After creating the service account, you can configure SkyPilot to use it through ``~/.sky/config.yaml``:

.. code-block:: yaml

    kubernetes:
      remote_identity: sky-sa   # Or your service account name

If you would like SkyPilot to automatically create the service account and roles, you can use the following config:

.. code-block:: yaml

    kubernetes:
      remote_identity: SERVICE_ACCOUNT  # Will automatically create the service account and roles

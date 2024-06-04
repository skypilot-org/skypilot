.. _cloud-permissions-kubernetes:

Kubernetes
==========

When running outside your Kubernetes cluster, SkyPilot uses your local ``~/.kube/config`` file
for authentication and creating resources on your Kubernetes cluster.

When running inside your Kubernetes cluster (e.g., as a Spot controller or Serve controller),
SkyPilot can operate using either of the following three authentication methods:

1. **Using your local kubeconfig file**: In this case, SkyPilot will
   copy your local ``~/.kube/config`` file to the controller pod and use it for
   authentication. This is the default method when running inside the cluster,
   and no additional configuration is required.

   .. note::

       If your cluster uses exec based authentication in your ``~/.kube/config`` file
       (e.g., GKE uses exec auth by default), SkyPilot may not be able to authenticate using this method. In this case,
       consider using the service account methods below.

2. **Creating a service account**: SkyPilot can automatically create the service
   account and roles for itself to manage resources in the Kubernetes cluster.
   To use this method, set ``remote_identity: SERVICE_ACCOUNT`` to your
   Kubernetes configuration in the :ref:`~/.sky/config.yaml <config-yaml>` file:

   .. code-block:: yaml

       kubernetes:
         remote_identity: SERVICE_ACCOUNT

   For details on the permissions that are granted to the service account,
   refer to the `Permissions required for SkyPilot`_ section below.

3. **Using a custom service account**: If you have a custom service account
   with the `necessary permissions <k8s-permissions_>`__, you can configure
   SkyPilot to use it by adding this to your :ref:`~/.sky/config.yaml <config-yaml>` file:

   .. code-block:: yaml

       kubernetes:
         remote_identity: your-service-account-name

.. note::

    Service account based authentication applies only when the remote SkyPilot
    cluster (including spot and serve controller) is launched inside the
    Kubernetes cluster. When running outside the cluster (e.g., on AWS),
    SkyPilot will use the local ``~/.kube/config`` file for authentication.

Below are the permissions required by SkyPilot and an example service account YAML that you can use to create a service account with the necessary permissions.

.. _k8s-permissions:

Permissions required for SkyPilot
---------------------------------

SkyPilot requires permissions equivalent to the following roles to be able to manage the resources in the Kubernetes cluster:

.. code-block:: yaml

    # Namespaced role for the service account
    # Required for creating pods, services and other necessary resources in the namespace.
    # Note these permissions only apply in the namespace where SkyPilot is deployed, and the namespace can be changed below.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-role
      namespace: default  # Change to your namespace if using a different one.
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
      namespace: default  # Change to your namespace if using a different one.
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
    # Optional: If using ingresses, role for accessing ingress service IP
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      namespace: ingress-nginx
      name: sky-sa-role-ingress-nginx
    rules:
      - apiGroups: [""]
        resources: ["services"]
        verbs: ["list", "get"]

.. tip::

    The sky-sa-role above

These roles must apply to both the user account configured in the kubeconfig file and the service account used by SkyPilot (if configured).

If your tasks use object store mounting (e.g., S3, GCS, etc.), you will need to also create a `skypilot-system` namespace and grant the necessary permissions to the service account in that namespace.

.. code-block:: yaml

    # Create namespace for SkyPilot
    apiVersion: v1
    kind: Namespace
    metadata:
      name: skypilot-system
      labels:
        parent: skypilot
    ---
    # Role for the skypilot-system namespace to create FUSE device manager and
    # any other system components required by SkyPilot.
    # This role must be bound in the skypilot-system namespace to the service account used for SkyPilot.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: skypilot-system-service-account-role
      namespace: skypilot-system  # Do not change this namespace
      labels:
        parent: skypilot
    rules:
      - apiGroups: ["*"]
        resources: ["*"]
        verbs: ["*"]

.. _k8s-sa-example:

Example using Custom Service Account
------------------------------------

To create a service account that has all necessary permissions for SkyPilot (including for accessing object stores), you can use the following YAML.
In the example below, the service account is named `sky-sa` and is created in the `default` namespace.
Change the namespace and service account name as needed.

.. code-block:: yaml

    # create-sky-sa.yaml
    kind: ServiceAccount
    apiVersion: v1
    metadata:
      name: sky-sa  # Change to your service account name
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    ---
    # Role for the service account
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-role
      namespace: default  # Change to your namespace if using a different one.
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
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
    roleRef:
      kind: Role
      name: sky-sa-role
      apiGroup: rbac.authorization.k8s.io
    ---
    # ClusterRole for the service account
    kind: ClusterRole
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-cluster-role
      namespace: default  # Change to your namespace if using a different one.
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
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
        namespace: default  # Change to your namespace if using a different one.
    roleRef:
      kind: ClusterRole
      name: sky-sa-cluster-role
      apiGroup: rbac.authorization.k8s.io
    ---
    # Optional: If using object store mounting, create the skypilot-system namespace
    apiVersion: v1
    kind: Namespace
    metadata:
      name: skypilot-system
      labels:
        parent: skypilot
    ---
    # Optional: If using object store mounting, create role in the skypilot-system
    # namespace to create FUSE device manager.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: skypilot-system-service-account-role
      namespace: skypilot-system  # Do not change this namespace
      labels:
        parent: skypilot
    rules:
      - apiGroups: ["*"]
        resources: ["*"]
        verbs: ["*"]
    ---
    # Optional: If using object store mounting, create rolebinding in the skypilot-system
    # namespace to create FUSE device manager.
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: sky-sa-skypilot-system-role-binding
      namespace: skypilot-system  # Do not change this namespace
      labels:
        parent: skypilot
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
        namespace: default  # Change this to the namespace where the service account is created
    roleRef:
      kind: Role
      name: skypilot-system-service-account-role
      apiGroup: rbac.authorization.k8s.io
    ---
    # Optional: Role for accessing ingress resources
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      name: sky-sa-role-ingress-nginx
      namespace: ingress-nginx  # Do not change this namespace
    rules:
      - apiGroups: [""]
        resources: ["services"]
        verbs: ["list", "get", "watch"]
      - apiGroups: ["rbac.authorization.k8s.io"]
        resources: ["roles", "rolebindings"]
        verbs: ["list", "get", "watch"]
    ---
    # Optional: RoleBinding for accessing ingress resources
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: sky-sa-rolebinding-ingress-nginx
      namespace: ingress-nginx  # Do not change this namespace
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
        namespace: default  # Change this to the namespace where the service account is created
    roleRef:
      kind: Role
      name: sky-sa-role-ingress-nginx
      apiGroup: rbac.authorization.k8s.io

Create the service account using the following command:

.. code-block:: bash

    $ kubectl apply -f create-sky-sa.yaml

After creating the service account, the cluster admin may distribute kubeconfigs with the `sky-sa` service account to users who need to access the cluster.

Users should configure SkyPilot to use the `sky-sa` service account through ``~/.sky/config.yaml``:

.. code-block:: yaml

    kubernetes:
      remote_identity: sky-sa   # Or your service account name

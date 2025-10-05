.. _cloud-permissions-kubernetes:

Kubernetes
==========

When running outside your Kubernetes cluster, SkyPilot uses your local ``~/.kube/config`` file
for authentication and creating resources on your Kubernetes cluster.

When running inside your Kubernetes cluster (e.g., as a remote API server, Job controller or Serve controller),
SkyPilot can operate using either of the following three authentication methods:

1. **Automatically create a service account**: SkyPilot can automatically create the service
   account and roles for itself to manage resources in the Kubernetes cluster.
   This is the default method when running inside the cluster, and no
   additional configuration is required.

   For details on the permissions that are granted to the service account,
   refer to the `Minimum Permissions Required for SkyPilot`_ section below.

2. **Using a custom service account**: If you have a custom service account
   with the `necessary permissions <k8s-permissions_>`__, you can configure
   SkyPilot to use it by adding this to your :ref:`~/.sky/config.yaml <config-yaml>` file:

   .. code-block:: yaml

       kubernetes:
         remote_identity: your-service-account-name

3. **Using your local kubeconfig file**: In this case, SkyPilot will
   copy your local ``~/.kube/config`` file to the controller pod and use it for
   authentication. To use this method, set ``remote_identity: LOCAL_CREDENTIALS`` to your
   Kubernetes configuration in the :ref:`~/.sky/config.yaml <config-yaml>` file:

   .. code-block:: yaml

       kubernetes:
         remote_identity: LOCAL_CREDENTIALS

   .. note::

       If your cluster uses exec based authentication in your ``~/.kube/config`` file
       (e.g., GKE uses exec auth by default), SkyPilot may not be able to authenticate using this method. In this case,
       consider using the service account methods below.

.. note::

    Service account based authentication applies only when the remote SkyPilot
    cluster (including spot and serve controller) is launched inside the
    Kubernetes cluster. When running outside the cluster (e.g., on AWS),
    SkyPilot will use the local ``~/.kube/config`` file for authentication.

Below are the permissions required by SkyPilot and an example service account YAML that you can use to create a service account with the necessary permissions.

.. _k8s-permissions:

Minimum permissions required for SkyPilot
-----------------------------------------

SkyPilot requires permissions equivalent to the following roles to be able to manage the resources in the Kubernetes cluster:

.. code-block:: yaml

    # Namespaced role for the service account
    # Required for creating pods, services and other necessary resources in the namespace.
    # Note these permissions only apply in the namespace where SkyPilot is deployed, and the namespace can be changed below.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-role  # Can be changed if needed
      namespace: default  # Change to your namespace if using a different one.
    rules:
      # Required for managing pods and their lifecycle
      - apiGroups: [ "" ]
        resources: [ "pods", "pods/status", "pods/exec", "pods/portforward" ]
        verbs: [ "*" ]
      # Required for managing services for SkyPilot Pods
      - apiGroups: [ "" ]
        resources: [ "services" ]
        verbs: [ "*" ]
      # Required for managing SSH keys
      - apiGroups: [ "" ]
        resources: [ "secrets" ]
        verbs: [ "*" ]
      # Required for retrieving reason when Pod scheduling fails.
      - apiGroups: [ "" ]
        resources: [ "events" ]
        verbs: [ "get", "list", "watch" ]
    ---
    # ClusterRole for accessing cluster-wide resources. Details for each resource below:
    kind: ClusterRole
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-cluster-role  # Can be changed if needed
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    rules:
      # Required for getting node resources.
      - apiGroups: [""]
        resources: ["nodes"]
        verbs: ["get", "list", "watch"]
      # Required for autodetecting the runtime class of the nodes.
      - apiGroups: ["node.k8s.io"]
        resources: ["runtimeclasses"]
        verbs: ["get", "list", "watch"]
      # Required for accessing storage classes.
      - apiGroups: ["storage.k8s.io"]
        resources: ["storageclasses"]
        verbs: ["get", "list", "watch"]


.. tip::

    If you are using a different namespace than ``default``, make sure to change the namespace in the above manifests.

These roles must apply to both the user account configured in the kubeconfig file and the service account used by SkyPilot (if configured).

If you need to view real-time GPU availability with ``sky show-gpus``, your tasks use object store mounting or your tasks require access to ingress resources, you will need to grant additional permissions as described below.

Permissions for ``sky show-gpus``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``sky show-gpus`` needs to list all pods across all namespaces to calculate GPU availability. To do this, SkyPilot needs the ``get`` and ``list`` permissions for pods in a ``ClusterRole``:

.. code-block:: yaml

    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
        name: sky-sa-cluster-role-pod-reader
    rules:
      - apiGroups: [""]
        resources: ["pods"]
        verbs: ["get", "list"]


.. tip::

    If this role is not granted to the service account, ``sky show-gpus`` will still work but it will only show the total GPUs on the nodes, not the number of free GPUs.


Permissions for object store mounting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If your tasks use object store mounting (e.g., S3, GCS, etc.), SkyPilot will need to run a DaemonSet to expose the FUSE device as a Kubernetes resource to SkyPilot pods.

To allow this, you will need to also create a ``skypilot-system`` namespace which will run the DaemonSet and grant the necessary permissions to the service account in that namespace.


.. code-block:: yaml

    # Required only if using object store mounting
    # Create namespace for SkyPilot system
    apiVersion: v1
    kind: Namespace
    metadata:
      name: skypilot-system  # Do not change this
      labels:
        parent: skypilot
    ---
    # Role for the skypilot-system namespace to create fusermount-server and
    # any other system components required by SkyPilot.
    # This role must be bound in the skypilot-system namespace to the service account used for SkyPilot.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: skypilot-system-service-account-role  # Can be changed if needed
      namespace: skypilot-system  # Do not change this namespace
      labels:
        parent: skypilot
    rules:
      - apiGroups: [ "*" ]
        resources: [ "apps" ]
        verbs: [ "daemonsets" ]


Permissions for using Ingress
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If your tasks use :ref:`Ingress <kubernetes-ingress>` for exposing ports, you will need to grant the necessary permissions to the service account in the ``ingress-nginx`` namespace.

.. code-block:: yaml

    # Required only if using ingresses
    # Role for accessing ingress service IP
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      namespace: ingress-nginx  # Do not change this
      name: sky-sa-role-ingress-nginx  # Can be changed if needed
    rules:
      - apiGroups: [""]
        resources: ["services"]
        verbs: ["list", "get"]


.. _k8s-sa-example:

Example using custom service account
------------------------------------

To create a service account that has all necessary permissions for SkyPilot (including for accessing object stores), you can use the following YAML.

.. tip::

    In this example, the service account is named ``sky-sa`` and is created in the ``default`` namespace.
    Change the namespace and service account name as needed.


.. code-block:: yaml
   :linenos:

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
      name: sky-sa-role  # Can be changed if needed
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    rules:
      # Required for managing pods and their lifecycle
      - apiGroups: [ "" ]
        resources: [ "pods", "pods/status", "pods/exec", "pods/portforward" ]
        verbs: [ "*" ]
      # Required for managing services for SkyPilot Pods
      - apiGroups: [ "" ]
        resources: [ "services" ]
        verbs: [ "*" ]
      # Required for managing SSH keys
      - apiGroups: [ "" ]
        resources: [ "secrets" ]
        verbs: [ "*" ]
      # Required for retrieving reason when Pod scheduling fails.
      - apiGroups: [ "" ]
        resources: [ "events" ]
        verbs: [ "get", "list", "watch" ]
    ---
    # RoleBinding for the service account
    kind: RoleBinding
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-rb  # Can be changed if needed
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
    roleRef:
      kind: Role
      name: sky-sa-role  # Use the same name as the role at line 14
      apiGroup: rbac.authorization.k8s.io
    ---
    # ClusterRole for the service account
    kind: ClusterRole
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: sky-sa-cluster-role  # Can be changed if needed
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    rules:
      - apiGroups: [""]
        resources: ["nodes"]  # Required for getting node resources.
        verbs: ["get", "list", "watch"]
      - apiGroups: ["node.k8s.io"]
        resources: ["runtimeclasses"]   # Required for autodetecting the runtime class of the nodes.
        verbs: ["get", "list", "watch"]
      - apiGroups: ["networking.k8s.io"]   # Required for exposing services through ingresses
        resources: ["ingressclasses"]
        verbs: ["get", "list", "watch"]
      - apiGroups: [""]                 # Required for `sky show-gpus` command
        resources: ["pods"]
        verbs: ["get", "list"]
      - apiGroups: ["storage.k8s.io"]   # Required for using volumes
        resources: ["storageclasses"]
        verbs: ["get", "list", "watch"]
    ---
    # ClusterRoleBinding for the service account
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: sky-sa-cluster-role-binding  # Can be changed if needed
      namespace: default  # Change to your namespace if using a different one.
      labels:
        parent: skypilot
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
        namespace: default  # Change to your namespace if using a different one.
    roleRef:
      kind: ClusterRole
      name: sky-sa-cluster-role  # Use the same name as the cluster role at line 43
      apiGroup: rbac.authorization.k8s.io
    ---
    # Optional: If using object store mounting, create the skypilot-system namespace
    apiVersion: v1
    kind: Namespace
    metadata:
      name: skypilot-system  # Do not change this
      labels:
        parent: skypilot
    ---
    # Optional: If using object store mounting, create role in the skypilot-system
    # namespace to create fusermount-server.
    kind: Role
    apiVersion: rbac.authorization.k8s.io/v1
    metadata:
      name: skypilot-system-service-account-role  # Can be changed if needed
      namespace: skypilot-system  # Do not change this namespace
      labels:
        parent: skypilot
    rules:
      - apiGroups: [ "apps" ]
        resources: [ "daemonsets" ]
        verbs: [ "*" ]
    ---
    # Optional: If using object store mounting, create rolebinding in the skypilot-system
    # namespace to create fusermount-server.
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
      name: skypilot-system-service-account-role  # Use the same name as the role above
      apiGroup: rbac.authorization.k8s.io
    ---
    # Optional: Role for accessing ingress resources
    apiVersion: rbac.authorization.k8s.io/v1
    kind: Role
    metadata:
      name: sky-sa-role-ingress-nginx  # Can be changed if needed
      namespace: ingress-nginx  # Do not change this namespace
      labels:
        parent: skypilot
    rules:
      - apiGroups: [""]
        resources: ["services"]
        verbs: ["list", "get", "watch"]
    ---
    # Optional: RoleBinding for accessing ingress resources
    apiVersion: rbac.authorization.k8s.io/v1
    kind: RoleBinding
    metadata:
      name: sky-sa-rolebinding-ingress-nginx  # Can be changed if needed
      namespace: ingress-nginx  # Do not change this namespace
      labels:
        parent: skypilot
    subjects:
      - kind: ServiceAccount
        name: sky-sa  # Change to your service account name
        namespace: default  # Change this to the namespace where the service account is created
    roleRef:
      kind: Role
      name: sky-sa-role-ingress-nginx  # Use the same name as the role above
      apiGroup: rbac.authorization.k8s.io

Create the service account using the following command:

.. code-block:: bash

    $ kubectl apply -f create-sky-sa.yaml

After creating the service account, the cluster admin may distribute kubeconfigs with the ``sky-sa`` service account to users who need to access the cluster.

Users should also configure SkyPilot to use the ``sky-sa`` service account through ``~/.sky/config.yaml``:

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      remote_identity: sky-sa   # Or your service account name

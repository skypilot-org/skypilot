.. _kubernetes-example-kueue:

Using SkyPilot with Kueue
=========================

Kueue is an open source Kubernetes job scheduler.
This example shows how SkyPilot can be usedwith Kueue to schedule jobs on Kubernetes.

Install Kueue
-------------

This example assumes a Kubernetes cluster is already set up.

To install Kueue, run the following command:

.. code-block:: bash

    # See https://github.com/kubernetes-sigs/kueue/releases for available versions.
    VERSION=v0.11.4
    kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/$VERSION/manifests.yaml

Kueue does not support scheduling plain pods out of the box. Since SkyPilot creates and manages workloads as pods,
Kueue config needs to be patched to support plain pods.

.. code-block:: bash

    # Extract and patch the config nd save it to /tmp/kueueconfig.yaml
    # This is required because SkyPilot creates and manages workloads as pods
    kubectl -n kueue-system get cm kueue-manager-config -o jsonpath={.data.controller_manager_config\\.yaml} | yq '.integrations.frameworks += ["pod"]' > /tmp/kueueconfig.yaml
    # Apply the changes from /tmp/kueueconfig.yaml created above
    kubectl -n kueue-system create cm kueue-manager-config --from_file=controller_manager_config.yaml=/tmp/kueueconfig.yaml --dry-run=client -o yaml | kubectl -n kueue-system apply -f -
    # Restart the kueue-controller-manager pod with the following command
    kubectl -n kueue-system rollout restart deployment kueue-controller-manager
    # Wait for the restart to complete
    kubectl -n kueue-system rollout status deployment kueue-controller-manager

Create multiple namespaces
--------------------------

Multiple teams may share the same Kubernetes cluster under their own namespaces.
To simulate this, two namespaces are created: ``team1`` and ``team2``.

.. code-block:: bash

    kubectl create namespace team1
    kubectl create namespace team2

Create a kueue resource flavor
------------------------------

A ResourceFlavor is an object that represents resource variations (such as CPU architecture, GPU type, etc.)
and allows you to associate them with cluster nodes through labels, taints and tolerations.

Here a default resource flavor is created to be used with Kueue.

``kueue-resource-flavor.yaml``:

.. code-block:: yaml

    apiVersion: kueue.x-k8s.io/v1beta1
    kind: ResourceFlavor
    metadata:
      name: "default-flavor"

To create the resource flavor above, save the snippet to ``kueue-resource-flavor.yaml`` and run the following command:

.. code-block:: bash

    kubectl apply -f kueue-resource-flavor.yaml

Create a kueue admission check and provisioning request config
--------------------------------------------------------------

By default, Kueue will admit all pods that fits within the cluster queue's resource quota.
However, there may be cases where the underlying cluster does not have the necessary resources,
regardless of the quota.

To address this, an admission check is created to check if the necessary resources are available
in the underlying cluster.

``kueue-admission-check.yaml``:

.. code-block:: yaml

    apiVersion: kueue.x-k8s.io/v1beta1
    kind: AdmissionCheck
    metadata:
      name: skypilot-kueue-prov
    spec:
      controllerName: kueue.x-k8s.io/provisioning-request
      parameters:
        apiGroup: kueue.x-k8s.io
        kind: ProvisioningRequestConfig
        name: skypilot-kueue-config
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: ProvisioningRequestConfig
    metadata:
      name: skypilot-kueue-config
    spec:
      provisioningClassName: check-capacity.autoscaling.x-k8s.io
      managedResources:
      - nvidia.com/gpu
      - cpu
      - memory

The ``ProvisioningClassConfig`` above uses ``ProvisioningClassName`` of ``check-capacity.autoscaling.x.k8s-io``.

This ProvisioningClass checks if necessary resources are available in the underlying cluster, and will attempt to
provision them via an autoscaler if one is available.

Different cloud providers may provide their own ``ProvisioningClassName`` for their own autoscalers.
For example, GKE provides ``queued-provisioning.gke.io`` which integrates with GKE autoscaling node pools.

To create the admission check and provisioning request config above, save the snippet to ``kueue-admission-check.yaml`` and run the following command:

.. code-block:: bash

    kubectl apply -f kueue-admission-check.yaml

Create cluster queues and local queues
--------------------------------------

Kueue has a two level hierarchy: cluster queues and local queues.

A cluster queue is a Kueue resource that defines the resource quota and admission checks.
A cluster queue is shared across all namespaces.

A local queue is a Kueue resource that defines the resource quota for a namespace.
A local queue is created in a namespace and is only applicable to that namespace.
A local queue points to a cluster queue. Multiple local queues can point to the same cluster queue.

Here, we create a cluster queue and two local queues, one for each team.

``kueue-one-cluster-queue.yaml``:

.. code-block:: yaml

    apiVersion: kueue.x-k8s.io/v1beta1
    kind: ClusterQueue
    metadata:
      name: "skypilot-cluster-queue"
    spec:
      namespaceSelector: {} # match all namespaces
      resourceGroups:
      - coveredResources: ["cpu", "memory", "nvidia.com/gpu"]
        flavors:
        - name: "default-flavor"
        # Adjust this value based on actual resource needs instead of "Infinite"
        resources:
        - name: "cpu"
          nominalQuota: 1000000000    # "Infinite" quota
        - name: "memory"
          nominalQuota: 1000000000Gi  # "Infinite" quota
        - name: "nvidia.com/gpu"
          nominalQuota: 1000000000    # "Infinite" quota
      admissionChecks:
      - skypilot-kueue-prov
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: LocalQueue
    metadata:
      # A local queue is in a namespace
      namespace: "team1"
      name: "skypilot-local-queue-team1"
    spec:
      clusterQueue: "skypilot-cluster-queue"
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: LocalQueue
    metadata:
      # A local queue is in a namespace
      namespace: "team2"
      name: "skypilot-local-queue-team2"
    spec:
      clusterQueue: "skypilot-cluster-queue"

In this setup, the two namespaces ``team1`` and ``team2`` share the same cluster queue ``skypilot-cluster-queue``.
The two teams (each using their respective namespaces) submit their jobs to their respective local queues
``skypilot-local-queue-team1`` and ``skypilot-local-queue-team2``.
The jobs from two teams are subject to the same quota defined in the cluster queue,
and the jobs from the two teams are ordered by their priority together.

To create the cluster and local queues above, save the snippet to ``kueue-one-cluster-queue.yaml`` and run the following command:

.. code-block:: bash

    kubectl apply -f kueue-one-cluster-queue.yaml

Alternatively, the two teams can have their own cluster queues as follows:

``kueue-two-cluster-queues.yaml``:

.. code-block:: yaml

    apiVersion: kueue.x-k8s.io/v1alpha1
    kind: Cohort
    metadata:
      name: "skypilot-cohort"
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: ClusterQueue
    metadata:
      name: "skypilot-cluster-queue-team1"
    spec:
      cohort: "skypilot-cohort"
      namespaceSelector: {} # match all namespaces
      resourceGroups:
      - coveredResources: ["cpu", "memory", "nvidia.com/gpu"]
        flavors:
        - name: "default-flavor"
        # Adjust this value based on actual resource needs instead of "Infinite"
        resources:
        - name: "cpu"
          nominalQuota: 1000000000    # "Infinite" quota
        - name: "memory"
          nominalQuota: 1000000000Gi  # "Infinite" quota
        - name: "nvidia.com/gpu"
          nominalQuota: 1000000000    # "Infinite" quota
      admissionChecks:
      - skypilot-kueue-prov
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: ClusterQueue
    metadata:
      name: "skypilot-cluster-queue-team2"
    spec:
      cohort: "skypilot-cohort"
      namespaceSelector: {} # match all namespaces
      resourceGroups:
      - coveredResources: ["cpu", "memory", "nvidia.com/gpu"]
        flavors:
        - name: "default-flavor"
        # Adjust this value based on actual resource needs instead of "Infinite"
        resources:
        - name: "cpu"
          nominalQuota: 1000000000    # "Infinite" quota
        - name: "memory"
          nominalQuota: 1000000000Gi  # "Infinite" quota
        - name: "nvidia.com/gpu"
          nominalQuota: 1000000000    # "Infinite" quota
      admissionChecks:
      - skypilot-kueue-prov
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: LocalQueue
    metadata:
      # A local queue is in a namespace
      namespace: "team1"
      name: "skypilot-local-queue-team1"
    spec:
      clusterQueue: "skypilot-cluster-queue-team1"
    ---
    apiVersion: kueue.x-k8s.io/v1beta1
    kind: LocalQueue
    metadata:
      # A local queue is in a namespace
      namespace: "team2"
      name: "skypilot-local-queue-team2"
    spec:
      clusterQueue: "skypilot-cluster-queue-team2"

In this setup, the two namespaces ``team1`` and ``team2`` have their own separate cluster queues.
Now, each team has their own quotas, and jobs from each team are ordered by their priority independently.

To create the cluster and local queues above, save the snippet to ``kueue-two-cluster-queues.yaml`` and run the following command:

.. code-block:: bash

    kubectl apply -f kueue-two-cluster-queues.yaml

Launch a SkyPilot job with Kueue
--------------------------------

To launch a SkyPilot job with Kueue, simply set the ``kueue.x-k8s.io/queue-name`` field in ``resources.labels`` section of the SkyPilot job YAML.

``skypilot-job.yaml``:

.. code-block:: yaml

    config:
      kubernetes:
        # a long provision timeout gives a job time to be scheduled
        # by Kueue if the job cannot be scheduled immediately.
        provision_timeout: 900

    resources:
      cloud: kubernetes
      labels:
        kueue.x-k8s.io/queue-name: skypilot-local-queue-team1

    num_nodes: 1

    run: |
      echo hello

Then launch the job in the respective namespace (``team1`` in the example above).

.. code-block:: bash

    kubectl config set-context --current --namespace=team1
    sky launch skypilot-job.yaml

Further reading
---------------

To learn more about Kueue, see the `Kueue documentation <https://kueue.x-k8s.io/docs/overview/>`_.

Specifically, the following sections describe concepts that can be used to manage SkyPilot jobs with Kueue more effectively:

- `Implement resource sharing between cluster queues and define hierarchical quotas <https://kueue.sigs.k8s.io/docs/concepts/cohort/>`_
- `Implement workload priorities <https://kueue.sigs.k8s.io/docs/concepts/workload_priority_class/>`_
- `Define multiple resource flavors <https://kueue.sigs.k8s.io/docs/concepts/resource_flavor/>`_
- `Set up gang scheduling of multiple pods <https://kueue.sigs.k8s.io/docs/tasks/run/plain_pods/#running-a-group-of-pods-to-be-admitted-together>`_
- `Use Kueue with multiple clusters <https://kueue.sigs.k8s.io/docs/concepts/multikueue/>`_

.. _multi-kubernetes:

Multiple Kubernetes Clusters
================================


SkyPilot allows you to manage dev pods, jobs and services across multiple Kubernetes clusters through a single pane of glass.

You may have multiple Kubernetes clusters for different:

* **Use cases**: e.g., a production cluster and a development/testing cluster.
* **Regions or clouds**: e.g., US and EU regions; or AWS and Lambda clouds.
* **Accelerators**: e.g., NVIDIA H100 cluster and a Google TPU cluster.
* **Configurations**: e.g., a small cluster for a single node and a large cluster for multiple nodes.
* **Kubernetes versions**: e.g., to upgrade a cluster from Kubernetes 1.20 to 1.21, you may create a new Kubernetes cluster to avoid downtime or unexpected errors.


.. image:: /images/multi-kubernetes.svg
    :width: 95%
    :align: center

.. original image: https://docs.google.com/presentation/d/1_NzqS_ccihsQKfbOTewPaH8D496zaHMuh-fvPsPf9y0/edit#slide=id.p

Configuration
-------------

Step 1: Set up credentials
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To work with multiple Kubernetes clusters, their credentials must be set up as individual `contexts <https://kubernetes.io/docs/tasks/access-application-cluster/configure-access-multiple-clusters/>`_ in your local ``~/.kube/config`` file.

For deploying new clusters and getting  credentials, see :ref:`kubernetes-deployment`.

For example, a ``~/.kube/config`` file may look like this:

.. code-block:: yaml

    apiVersion: v1
    clusters:
    - cluster:
        certificate-authority-data:
        ...
        server: https://xx.xx.xx.xx:45819
      name: my-h100-cluster
    - cluster:
        certificate-authority-data:
        ...
        server: https://yy.yy.yy.yy:45819
      name: my-tpu-cluster
    contexts:
    - context:
        cluster: my-h100-cluster
        user: my-h100-cluster
      name: my-h100-cluster
    - context:
        cluster: my-tpu-cluster
        namespace: my-namespace
        user: my-tpu-cluster
      name: my-tpu-cluster
    current-context: my-h100-cluster
    ...


In this example, we have two Kubernetes clusters: ``my-h100-cluster`` and ``my-tpu-cluster``, and each Kubernetes cluster has a context for it.

Step 2: Set up SkyPilot to access multiple Kubernetes clusters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unlike clouds, SkyPilot does not failover through different Kubernetes clusters
(regions) by default because each Kubernetes cluster can have a different
purpose.

By default, SkyPilot only uses the context set in the ``current-context`` in the
kubeconfig. You can get the current context with ``kubectl config
current-context``.

To allow SkyPilot to access multiple Kubernetes clusters, you can set the
``kubernetes.allowed_contexts`` in the SkyPilot :ref:`global config <config-yaml>`, ``~/.sky/config.yaml``.

.. code-block:: yaml

    kubernetes:
      allowed_contexts:
        - my-h100-cluster
        - my-tpu-cluster

To check the enabled Kubernetes clusters, you can run ``sky check k8s``.

.. code-block:: console

    $ sky check k8s

    🎉 Enabled clouds 🎉
      ✔ Kubernetes
        Allowed contexts:
        ├── my-h100-cluster
        └── my-tpu-cluster

To check GPUs available in a Kubernetes cluster, you can run ``sky show-gpus --infra k8s``.

.. code-block:: console

    $ sky show-gpus --infra k8s
    Kubernetes GPUs
    GPU   UTILIZATION
    H100  16 of 16 free  
    A100  8 of 8 free    
    Context: my-h100-cluster
    GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION          
    H100  1, 2, 4, 8                16 of 16 free  
    Context: kind-skypilot
    GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION          
    A100  1, 2, 4, 8                8 of 8 free  
    Kubernetes per-node GPU availability
    CONTEXT          NODE                                          GPU       UTILIZATION        
    my-h100-cluster  gke-skypilotalpha-default-pool-ff931856-6uvd  -         0 of 0 free  
    my-h100-cluster  gke-skypilotalpha-largecpu-05dae726-1usy      H100      8 of 8 free  
    my-h100-cluster  gke-skypilotalpha-largecpu-05dae726-4rxa      H100      8 of 8 free  
    kind-skypilot    skypilot-control-plane                        A100      8 of 8 free  


Failover across multiple Kubernetes clusters
--------------------------------------------

With the ``kubernetes.allowed_contexts`` config set, SkyPilot will failover
through the Kubernetes clusters in the same order as they are specified in the field.


.. code-block:: console

    $ sky launch --gpus H100 --infra k8s echo 'Hello World'

    Considered resources (1 node):
    ---------------------------------------------------------------------------------------------------------
     INFRA                           INSTANCE          vCPUs   Mem(GB)   GPUS     COST ($)   CHOSEN
    ---------------------------------------------------------------------------------------------------------
     Kubernetes (my-eks-cluster)     2CPU--2GB         2       2         -        0.00       ✔
     Kubernetes (gke-skypilot)       4CPU--8GB         4       8         -        0.00      
     AWS (us-east-1)                 m6i.large         2       8         -        0.10     
     GCP (us-central1-a)             n2-standard-2     2       8         -        0.10     
    ---------------------------------------------------------------------------------------------------------


Launching in a specific Kubernetes cluster
------------------------------------------

SkyPilot uses the ``infra`` field to denote a Kubernetes context. You can point to a Kubernetes cluster
by specifying the ``--infra`` with the context name for that cluster.

.. code-block:: console


    $ # Launch in a specific Kubernetes cluster.
    $ sky launch --infra k8s/my-tpu-cluster echo 'Hello World'

    $ # Check the GPUs available in a Kubernetes cluster
    $ sky show-gpus --infra k8s/my-h100-cluster
    Kubernetes GPUs
    Context: my-h100-cluster
    GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION
    H100  1, 2, 4, 8                16 of 16 free  
    Kubernetes per-node GPU availability
    CONTEXT          NODE                                          GPU       UTILIZATION
    my-h100-cluster  gke-skypilotalpha-default-pool-ff931856-6uvd  -         0 of 0 free  
    my-h100-cluster  gke-skypilotalpha-largecpu-05dae726-1usy      H100      8 of 8 free  
    my-h100-cluster  gke-skypilotalpha-largecpu-05dae726-4rxa      H100      8 of 8 free  

When launching a SkyPilot cluster or task, you can also specify the context name with ``--infra`` to launch the cluster or task in.


Per-context configuration
-------------------------

When using multiple Kubernetes clusters, SkyPilot allows you to configure different settings for each Kubernetes context using the ``kubernetes.context_configs`` field in your ``~/.sky/config.yaml``.

This is useful when different clusters have different config requirements or capabilities.

You can specify per-context configurations for any Kubernetes config field, including:

* ``pod_config``: Custom `pod specifications <https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.27/#pod-v1-core>`_ (labels, annotations, volume mounts, runtime class, etc.)
* ``remote_identity``: Service account to use for the context
* ``provision_timeout``: Timeout for provisioning pods if autoscaler is used

See :ref:`Kubernetes config<config-yaml-kubernetes>` for the list of all fields supported.

Example configuration:

.. code-block:: yaml

    kubernetes:
      # Global defaults for all contexts
      provision_timeout: 10
      allowed_contexts:
        - my-h100-cluster
        - dev-cluster
      
      # Context-specific configurations
      context_configs:
        my-h100-cluster:
          # Use NVIDIA runtime for H100 cluster
          pod_config:
            metadata:
              labels:
                cluster-type: production
          remote_identity: h100-service-account # Use a custom service account for the cluster
        # Development cluster with different proxy settings and volume mounts
        dev-cluster:
          pod_config:
            spec:
              imagePullSecrets:
                - name: my-secret
            containers:
              - env:
                  - name: HTTP_PROXY
                    value: http://proxy-host:3128
                volumeMounts:
                  - mountPath: /foo
                    name: example-volume
                    readOnly: true
            volumes:
              - name: example-volume
                hostPath:
                    path: /tmp
                    type: Directory
          provision_timeout: 3600 # Large timeout for autoscaler to provision nodes
          autoscaler: gke


Dynamically updating clusters to use
----------------------------------------------

You can configure SkyPilot to dynamically fetch Kubernetes cluster configs and enforce restrictions on which clusters are used. Refer to :ref:`dynamic-kubernetes-contexts-update-policy` for more.

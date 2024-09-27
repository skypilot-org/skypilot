.. _multi-kubernetes:

Across Multiple Kubernetes Clusters
===================================


SkyPilot allows you to manage dev pods, jobs and services across multiple Kubernetes clusters in a single pane of glass.

You may have multiple Kubernetes clusters for a variety of reasons:

* Clusters for different purposes: e.g.,a production cluster and a development/testing cluster.
* Clusters in different regions or clouds: e.g., US and EU regions; or AWS and Lambda clouds.
* Clusters for different accelerators: e.g., NVIDIA H100 cluster and a Google TPU cluster.
* Clusters with different configurations: e.g., a small cluster for a single node and a large cluster for multiple nodes.
* Clusters for different Kubernetes versions: e.g., to upgrade a cluster from Kubernetes 1.20 to 1.21, you may create a new Kubernetes cluster to avoid downtime or unexpected errors.


.. image:: /images/kubernetes/multi-kubernetes.png


Set Up Credentials for Multiple Kubernetes Clusters
---------------------------------------------------

To work with multiple Kubernetes clusters, you need to ensure you have the necessary credentials for each cluster. To get
it work with SkyPilot, you don't have to do any additional setup than having those credentials in your local ``~/.kube/config`` file.

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

Point to a Kubernetes Cluster and Launch
-----------------------------------------

SkyPilot borrows the ``region`` concept from clouds to denote a Kubernetes cluster. You can point to a Kubernetes cluster
by specifying the ``--region`` with the context name for that cluster.

.. code-block:: console

    # Check the GPUs available in a Kubernetes cluster
    $ sky show-gpus --cloud kubernetes --region my-h100-cluster

    Kubernetes GPUs (Context: my-h100-cluster)
    GPU    QTY_PER_NODE            TOTAL_GPUS  TOTAL_FREE_GPUS  
    H100   1, 2, 3, 4, 5, 6, 7, 8  8           8                

    Kubernetes per node GPU availability
    NODE_NAME                                 GPU_NAME  TOTAL_GPUS  FREE_GPUS  
    gke-test-zhwu-default-pool-20159504-hbzn  H100      8           8
    gke-test-zhwu-default-pool-20159504-w5x7  None      0           0

When launching a SkyPilot cluster or task, you can also specify the context name with ``--region`` to launch the cluster or task in.

.. code-block:: console

    $ sky launch --cloud kubernetes --region my-tpu-cluster echo 'Hello World'


.. note::

    When you don't specify a region, SkyPilot will use the current context.


Failover across Multiple Kubernetes Clusters
--------------------------------------------

SkyPilot enables you to failover across multiple Kubernetes clusters. It is useful when you have multiple Kubernetes clusters
across different clouds and regions, and you want to launch a task in any of the clusters with available GPUs.

Different from cloud providers, SkyPilot does not failover through different regions (contexts) by default, because multiple
Kubernetes clusters can be for different purposes. To enable the failover, you can specify the ``kubernetes.allowed_contexts``
in SkyPilot config, ``~/.sky/config.yaml`` (See config YAML spec: :ref:`config-yaml`).

.. code-block:: yaml

    kubernetes:
      allowed_contexts:
        - my-h100-cluster-gke
        - my-h100-cluster-eks

With this global config, SkyPilot will failover through the Kubernetes clusters in the ``allowed_contexts`` with in the same
order as they are specified.


.. code-block:: console

    $ sky launch --cloud kubernetes echo 'Hello World'

    Considered resources (1 node):
    ------------------------------------------------------------------------------------------------------------
    CLOUD        INSTANCE           vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE           COST ($)   CHOSEN   
    ------------------------------------------------------------------------------------------------------------
    Kubernetes   2CPU--8GB--1H100   2       8         H100:1         my-h100-cluster-gke   0.00          ✔     
    Kubernetes   2CPU--8GB--1H100   2       8         H100:1         my-h100-cluster-eks   0.00                
    ------------------------------------------------------------------------------------------------------------



Dynamically Update Kubernetes Clusters to Use
----------------------------------------------

To see how to dynamically update Kubernetes clusters to use, refer to :ref:`dynamic-kubernetes-contexts-update-policy`.


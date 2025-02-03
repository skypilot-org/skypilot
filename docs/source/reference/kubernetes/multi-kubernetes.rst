.. _multi-kubernetes:

Multiple Kubernetes Clusters
================================


SkyPilot allows you to manage dev pods, jobs and services across multiple Kubernetes clusters through a single pane of glass.

You may have multiple Kubernetes clusters for different:

* **Use cases**, e.g., a production cluster and a development/testing cluster.
* **Regions or clouds**, e.g., US and EU regions; or AWS and Lambda clouds.
* **Accelerators**, e.g., NVIDIA H100 cluster and a Google TPU cluster.
* **Configurations**, e.g., a small cluster for a single node and a large cluster for multiple nodes.
* **Kubernetes versions**, e.g., to upgrade a cluster from Kubernetes 1.20 to 1.21, you may create a new Kubernetes cluster to avoid downtime or unexpected errors.


.. image:: /images/multi-kubernetes.svg
    :width: 80%
    :align: center

.. original image: https://docs.google.com/presentation/d/1_NzqS_ccihsQKfbOTewPaH8D496zaHMuh-fvPsPf9y0/edit#slide=id.p

Configuration
-------------

Step 1: Set Up Credentials
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

Step 2: Setup SkyPilot to Access Multiple Kubernetes Clusters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Unlike clouds, SkyPilot does not failover through different Kubernetes clusters (regions) by default because each Kubernetes clusters can have a different purpose.

By default, SkyPilot only uses the context set as the ``current-context`` in the kubeconfig. You can get the current context with ``kubectl config current-context``.

To allow SkyPilot to access multiple Kubernetes clusters, you can set the ``kubernetes.allowed_contexts`` in the SkyPilot config.

.. code-block:: yaml

    kubernetes:
      allowed_contexts:
        - my-h100-cluster
        - my-tpu-cluster

To check the enabled Kubernetes clusters, you can run ``sky check kubernetes``.

.. code-block:: console

    $ sky check kubernetes

    🎉 Enabled clouds 🎉
      ✔ Kubernetes
        Allowed contexts:
        ├── my-h100-cluster
        └── my-tpu-cluster


Failover across Multiple Kubernetes Clusters
--------------------------------------------

With the ``kubernetes.allowed_contexts`` global config, SkyPilot failover through the Kubernetes clusters in the ``allowed_contexts`` in the same
order as they are specified.


.. code-block:: console

    $ sky launch --gpus H100 --cloud kubernetes echo 'Hello World'

    Considered resources (1 node):
    ------------------------------------------------------------------------------------------------------------
    CLOUD        INSTANCE           vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE           COST ($)   CHOSEN   
    ------------------------------------------------------------------------------------------------------------
    Kubernetes   2CPU--8GB--1H100   2       8         H100:1         my-h100-cluster-gke   0.00          ✔     
    Kubernetes   2CPU--8GB--1H100   2       8         H100:1         my-h100-cluster-eks   0.00                
    ------------------------------------------------------------------------------------------------------------


Point to a Kubernetes Cluster and Launch
-----------------------------------------

SkyPilot borrows the ``region`` concept from clouds to denote a Kubernetes context. You can point to a Kubernetes cluster
by specifying the ``--region`` with the context name for that cluster.

.. code-block:: console

    $ # Check the GPUs available in a Kubernetes cluster
    $ sky show-gpus --cloud kubernetes --region my-h100-cluster

    Kubernetes GPUs (Context: my-h100-cluster)
    GPU    QTY_PER_NODE            TOTAL_GPUS  TOTAL_FREE_GPUS  
    H100   1, 2, 3, 4, 5, 6, 7, 8  8           8                

    Kubernetes per node GPU availability
    NODE_NAME                                 GPU_NAME  TOTAL_GPUS  FREE_GPUS  
    my-h100-cluster-hbzn  H100      8           8
    my-h100-cluster-w5x7  None      0           0

When launching a SkyPilot cluster or task, you can also specify the context name with ``--region`` to launch the cluster or task in.

.. code-block:: console

    $ sky launch --cloud kubernetes --region my-tpu-cluster echo 'Hello World'


Dynamically Update Kubernetes Clusters to Use
----------------------------------------------

You can have configure SkyPilot to dynamically fetch Kubernetes cluster configs and enforce restrictions on which clusters are used. Refer to :ref:`dynamic-kubernetes-contexts-update-policy` for more.


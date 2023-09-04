.. _kubernetes-overview:

SkyPilot on Kubernetes (Alpha)
=============================

.. note::
    Kubernetes support for SkyPilot is a alpha preview under active development.
    There may be rough edges and features may change without notice.
    Please report any `bugs <https://github.com/skypilot-org/skypilot/issues>`_ and
    `reach out to us <http://slack.skypilot.co>`_ for feature requests.

SkyPilot tasks can be deployed on your private on-prem or cloud clusters running Kubernetes.
The Kubernetes cluster gets added to the list of "clouds" in SkyPilot and SkyPilot
tasks can be submitted to your Kubernetes cluster just like any other cloud provider.

**Benefits of bringing your Kubernetes cluster to SkyPilot:**

* Get SkyPilot features (setup management, job execution, queuing, logging, SSH access) on your Kubernetes resources
* Replace complex Kubernetes manifests with simple SkyPilot tasks
* Maximize resource utilization by running cloud jobs on your Kubernetes cluster.
* Seamlessly "burst" jobs to the cloud if the Kubernetes cluster is congested.
* Retain observability and control over your cluster with your existing Kubernetes tools

**Supported deployment models:**

* On-prem clusters (Kubeadm, K3s, Rancher)
* Hosted Kubernetes services (AWS EKS, GKE)
* Local development clusters (KinD, minikube)


Kubernetes Cluster Requirements
-------------------------------

To connect and use a Kubernetes cluster, SkyPilot needs:

* An existing Kubernetes cluster running Kubernetes v1.20 or later.
* A `Kubeconfig <kubeconfig>`_ file containing access credentials and namespace to be used.

In a typical workflow:

1. A cluster administrator sets up a Kubernetes cluster. Detailed guides for
   different deployment environments (Amazon EKS, Google GKE, On-Prem and local debugging) are included in the :ref:`Kubernetes cluster setup guide <kubernetes-setup>`.

2. Users who want to run SkyPilot tasks on this cluster are issued Kubeconfig
   files containing their credentials (`kube-context <https://kubernetes.io/docs/tasks/tools/>`_).
   SkyPilot reads this Kubeconfig file and adds the cluster to list clouds considered when launching tasks.

Submitting SkyPilot tasks to Kubernetes Clusters
------------------------------------------------
.. _kubernetes-instructions:

Once your Kubernetes cluster is up and running:

0. Make sure `Kubectl <https://kubernetes.io/docs/tasks/tools/>`_, `socat <https://kubernetes.io/docs/tasks/tools/>`_ and `lsof <https://kubernetes.io/docs/tasks/tools/>`_ are installed on the machine which will submit tasks.

1. Place your kubeconfig file at ``~/.kube/config``.

   .. code-block:: console

     $ mkdir -p ~/.kube
     $ cp /path/to/kubeconfig ~/.kube/config

   You can verify your credentials are setup correctly by running :code:`kubectl get pods`

2. **[If using GPUs, not required for GKE clusters]** If your Kubernetes cluster has Nvidia GPUs, make sure you have the Nvidia device plugin installed (i.e., ``nvidia.com/gpu`` resource is available on each node). Additionally, you will need to label each node in your cluster with the GPU type. For example, a node with v100 GPUs must have a label :code:`skypilot.co/accelerators: v100`. We provide a convinience script that automatically detects GPU type and labels each node. You can run it with:

   .. code-block:: console

     $ python -m sky.utils.kubernetes.gpu_labeler

     Created GPU labeler job for node ip-192-168-54-76.us-west-2.compute.internal
     Created GPU labeler job for node ip-192-168-93-215.us-west-2.compute.internal
     GPU labeling started - this may take a few minutes to complete.
     To check the status of GPU labeling jobs, run `kubectl get jobs --namespace=kube-system -l job=sky-gpu-labeler`
     You can check if nodes have been labeled by running `kubectl describe nodes` and looking for labels of the format `skypilot.co/accelerators: <gpu_name>`.


   .. note::
     GPU labelling is not required on GKE clusters - SkyPilot will automatically use GKE provided labels. However, you will still need to install `drivers <https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers>`_.


   .. note::
     To cleanup any leftover jobs from the GPU labelling process, run ``python -m sky.utils.kubernetes.gpu_labeler --cleanup``.


2. Run :code:`sky check` and verify that Kubernetes is enabled in SkyPilot.

   .. code-block:: console

     $ sky check

     Checking credentials to enable clouds for SkyPilot.
     ...
     Kubernetes: enabled
     ...


   .. note::
     :code:`sky check` will also check if GPU support is available on your cluster. If GPU support is not available, it
     will show the reason.
     To setup GPU support on the cluster, refer to the :ref:`Kubernetes cluster setup guide <kubernetes-setup>`.

4. You can now run any SkyPilot task on your Kubernetes cluster.

   .. code-block:: console

        $ sky launch --cpus 2+
        == Optimizer ==
        Target: minimizing cost
        Estimated cost: $0.0 / hour

        Considered resources (1 node):
        ---------------------------------------------------------------------------------------------------
         CLOUD        INSTANCE          vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN
        ---------------------------------------------------------------------------------------------------
         Kubernetes   2CPU--2GB         2       2         -              kubernetes    0.00          ✔
         AWS          m6i.large         2       8         -              us-east-1     0.10
         Azure        Standard_D2s_v5   2       8         -              eastus        0.10
         GCP          n2-standard-2     2       8         -              us-central1   0.10
         IBM          bx2-8x32          8       32        -              us-east       0.38
         Lambda       gpu_1x_a10        30      200       A10:1          us-east-1     0.60
        ---------------------------------------------------------------------------------------------------.


.. note::
  SkyPilot will use the cluster and namespace set in the ``current-context`` in the
  kubeconfig file. To manage your ``current-context``:

  .. code-block:: console

    $ # See current context
    $ kubectl config current-context

    $ # Switch current-context
    $ kubectl config use-context mycontext

    $ # Set a specific namespace to be used in the current-context
    $ kubectl config set-context --current --namespace=mynamespace


Observability
-------------
Hi.


FAQs
----

* **Are autoscaling Kubernetes clusters supported?**

  Yes - however they currently require adjusting the resource provisioning timeout (:code:`Kubernetes.TIMEOUT` in `clouds/kubernetes.py`) to a large value to give enough time for the cluster to autoscale. We are working on a better interface to adjust this timeout - stay tuned!

* **What container image is used for tasks? Can I specify my own image?**

  We use and maintain a SkyPilot container image that has conda and a few other basic tools installed. You can specify a custom image to use in `clouds/kubernetes.py`, but it must have rsync, conda and OpenSSH server installed. We are working on a interface to allow specifying custom images through the :code:`image_id` field in the task YAML - stay tuned!

* **Can SkyPilot provision a Kubernetes cluster for me? Will SkyPilot add more nodes to my Kubernetes clusters?**

  The goal of SkyPilot on Kubernetes is to run SkyPilot tasks on resources in an existing Kubernetes cluster. It does not provision any new Kubernetes clusters or add new nodes to an existing Kubernetes cluster. The Kubernetes control plane remains untouched.

Features and Roadmap
--------------------

SkyPilot on Kubernetes is under active development. Some features are in progress and will be released soon:

* CPU Tasks - ✅ Available
* Auto-down - ✅ Available
* Storage mounting - ✅ Available on x86_64 clusters
* GPU Tasks - ✅ Available
* Multi-node tasks - 🚧 In progress
* Multiple Kubernetes Clusters - 🚧 In progress



Table of Contents
-------------------
.. toctree::
   :hidden:

   kubernetes-setup

.. _kubernetes-overview:

Running on Kubernetes
=============================

.. note::
    Kubernetes support is under active development. `Please share your feedback <https://forms.gle/KmAtyNhEysiw2ZCR7>`_
    or `directly reach out to the development team <http://slack.skypilot.co>`_
    for feature requests and more.

SkyPilot tasks can be run on your private on-prem or cloud Kubernetes clusters.
The Kubernetes cluster gets added to the list of "clouds" in SkyPilot and SkyPilot
tasks can be submitted to your Kubernetes cluster just like any other cloud provider.

**Benefits of using SkyPilot to run jobs on your Kubernetes cluster:**

* Get SkyPilot features (setup management, job execution, queuing, logging, SSH access) on your Kubernetes resources
* Replace complex Kubernetes manifests with simple SkyPilot tasks
* Seamlessly "burst" jobs to the cloud if your Kubernetes cluster is congested
* Retain observability and control over your cluster with your existing Kubernetes tools

**Supported Kubernetes deployments:**

* Hosted Kubernetes services (EKS, GKE)
* On-prem clusters (Kubeadm, K3s, Rancher)
* Local development clusters (KinD, minikube)


Kubernetes Cluster Requirements
-------------------------------

To connect and use a Kubernetes cluster, SkyPilot needs:

* An existing Kubernetes cluster running Kubernetes v1.20 or later.
* A `Kubeconfig <https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/>`_ file containing access credentials and namespace to be used.

In a typical workflow:

1. A cluster administrator sets up a Kubernetes cluster. Detailed admin guides for
   different deployment environments (Amazon EKS, Google GKE, On-Prem and local debugging) are included in the :ref:`Kubernetes cluster setup guide <kubernetes-setup>`.

2. Users who want to run SkyPilot tasks on this cluster are issued Kubeconfig
   files containing their credentials (`kube-context <https://kubernetes.io/docs/tasks/access-application-cluster/configure-access-multiple-clusters/#define-clusters-users-and-contexts>`_).
   SkyPilot reads this Kubeconfig file to communicate with the cluster.

Submitting SkyPilot tasks to Kubernetes Clusters
------------------------------------------------
.. _kubernetes-instructions:

Once your cluster administrator has :ref:`setup a Kubernetes cluster <kubernetes-setup>` and provided you with a kubeconfig file:

0. Make sure `kubectl <https://kubernetes.io/docs/tasks/tools/>`_, ``socat`` and ``nc`` (netcat) are installed on your local machine.

   .. code-block:: console

     $ # MacOS
     $ brew install kubectl socat netcat

     $ # Linux (may have socat already installed)
     $ sudo apt-get install kubectl socat netcat


1. Place your kubeconfig file at ``~/.kube/config``.

   .. code-block:: console

     $ mkdir -p ~/.kube
     $ cp /path/to/kubeconfig ~/.kube/config

   You can verify your credentials are setup correctly by running :code:`kubectl get pods`.

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

        $ sky launch --cpus 2+ task.yaml
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

FAQs
----

* **Are autoscaling Kubernetes clusters supported?**

  To run on an autoscaling cluster, you may need to adjust the resource provisioning timeout (:code:`Kubernetes.TIMEOUT` in `clouds/kubernetes.py`) to a large value to give enough time for the cluster to autoscale. We are working on a better interface to adjust this timeout - stay tuned!

* **What container image is used for tasks? Can I specify my own image?**

  We use and maintain a SkyPilot container image that has conda and a few other basic tools installed. You can specify a custom image to use in `clouds/kubernetes.py`, but it must have rsync, conda and OpenSSH server installed. We are working on a interface to allow specifying custom images through the :code:`image_id` field in the task YAML - stay tuned!

* **Can SkyPilot provision a Kubernetes cluster for me? Will SkyPilot add more nodes to my Kubernetes clusters?**

  The goal of Kubernetes support is to run SkyPilot tasks on an existing Kubernetes cluster. It does not provision any new Kubernetes clusters or add new nodes to an existing Kubernetes cluster.

* **I have multiple users in my organization who share the same Kubernetes cluster. How do I provide isolation for their SkyPilot workloads?**

  For isolation, you can create separate Kubernetes namespaces and set them in the kubeconfig distributed to users. SkyPilot will use the namespace set in the kubeconfig for running all tasks.

Features and Roadmap
--------------------

Kubernetes support is under active development. Some features are in progress and will be released soon:

* CPU and GPU Tasks - ✅ Available
* Auto-down - ✅ Available
* Storage mounting - ✅ Available on x86_64 clusters
* Multi-node tasks - ✅ Available
* Opening ports and exposing services - 🚧 In progress
* Custom images - 🚧 In progress
* Multiple Kubernetes Clusters - 🚧 In progress


.. toctree::
   :hidden:

   kubernetes-setup

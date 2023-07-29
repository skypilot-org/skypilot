.. _kubernetes-overview:

SkyPilot on Kubernetes (Beta)
=============================

.. note::
    Kubernetes support for SkyPilot is a beta preview under active development. There may be rough edges and features may change without notice. Please report any `bugs <https://github.com/skypilot-org/skypilot/issues>`_ and `reach out to us <http://slack.skypilot.co>`_ for feature requests.

SkyPilot can run on your private on-prem or cloud Kubernetes clusters.
Your Kubernetes cluster gets added to the list of "clouds" in SkyPilot and SkyPilot
tasks can be submitted to your Kubernetes cluster just like any other cloud provider.

**Benefits of bringing your Kubernetes cluster to SkyPilot:**

* Get SkyPilot features (setup management, job execution, queuing, logging, SSH access) on your Kubernetes resources
* Replace complex Kubernetes manifests with simple SkyPilot tasks
* Maximize resource utilization by running cloud jobs on your Kubernetes cluster.
* Seamlessly "burst" jobs to the cloud if the Kubernetes cluster is congested.

**Supported deployment models:**

* On-prem clusters (Kubeadm, K3s, Rancher)
* Hosted Kubernetes services (AWS EKS, GKE)
* Local development clusters (KinD, minikube)


Kubernetes Cluster Requirements
-------------------------------

To connect and use a Kubernetes cluster, SkyPilot needs:

* A `Kubeconfig <kubeconfig>`_ file containing access credentials and namespace to be used.
* Ports 30000-32767 should be accessible on all Kubernetes nodes.

Detailed guides for setting up different deployment environments (Amazon EKS, Google GKE, On-Prem and local debugging) are included in the :ref:`Kubernetes cluster setup guide <kubernetes-setup>`.

Using Kubernetes Clusters with SkyPilot
---------------------------------------
.. _kubernetes-instructions:

Once your Kubernetes cluster is up and running:

1. Place your kubeconfig file at ``~/.kube/config``.

   .. code-block:: console

     $ mkdir -p ~/.kube
     $ cp /path/to/kubeconfig ~/.kube/config

2. Run :code:`sky check` and verify that Kubernetes is enabled in SkyPilot.

   .. code-block:: console

     $ sky check

     Checking credentials to enable clouds for SkyPilot.
     ...
     Kubernetes: enabled
     ...


3. You can now run any SkyPilot task on your Kubernetes cluster.

   .. code-block:: console

     $ sky launch -c mycluster task.yaml

     TODO(romilb): Add output here.


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

  Yes - however they currently require adjusting the resource provisioning timeout (:code:`Kubernetes.TIMEOUT` in `clouds/kubernetes.py`) to a large value to give enough time for the cluster to autoscale. We are working on a better interface to adjust this timeout - stay tuned!

* **What container image is used for tasks? Can I specify my own image?**

  We use and maintain a SkyPilot container image that has conda and a few other basic tools installed. You can specify a custom image to use in `clouds/kubernetes.py`, but it must have rsync and conda installed. We are working on a interface to allow specifying custom images through the :code:`image_id` field in the task YAML - stay tuned!

* **Will SkyPilot provision a Kubernetes cluster for me? Will SkyPilot add more nodes to my Kubernetes clusters?**

  The goal of SkyPilot on Kubernetes is to run SkyPilot tasks on resources in an existing Kubernetes cluster. It does not provision any new Kubernetes clusters or add new nodes to an existing Kubernetes cluster. The Kubernetes control plane remains untouched.


Features and Roadmap
--------------------

SkyPilot on Kubernetes is under active development. Some features are in progress and will be released soon:

* CPU Tasks - ✅ Available
* Auto-down - ✅ Available
* Storage mounting - ✅ (supported only on x86_64 clusters)
* GPU Tasks - 🚧 In progress
* Multi-node tasks - 🚧 In progress
* Multiple Kubernetes Clusters - 🚧 In progress



Table of Contents
-------------------
.. toctree::
   :hidden:

   kubernetes-setup
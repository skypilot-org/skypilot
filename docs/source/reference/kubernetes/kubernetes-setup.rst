.. _kubernetes-setup:

Kubernetes Cluster Setup
========================


.. note::
    This is a guide for cluster administrators on how to set up Kubernetes clusters
    for use with SkyPilot.

    If you are a SkyPilot user and your cluster administrator has already set up a cluster
    and shared a kubeconfig file with you, :ref:`Submitting tasks to Kubernetes <kubernetes-instructions>`
    explains how to submit tasks to your cluster.


SkyPilot's Kubernetes support is designed to work with most Kubernetes distributions and deployment environments.

To connect to a Kubernetes cluster, SkyPilot needs:

* An existing Kubernetes cluster running Kubernetes v1.20 or later.
* A `Kubeconfig <kubeconfig>`_ file containing access credentials and namespace to be used.


Deployment Guides
-----------------
Below we show minimal examples to set up a new Kubernetes cluster in different environments, including hosted services on the cloud, and generating kubeconfig files which can be :ref:`used by SkyPilot <kubernetes-instructions>`.

..
  TODO(romilb) - Add a table of contents/grid cards for each deployment environment.

* :ref:`Deploying locally on your laptop <kubernetes-setup-kind>`
* :ref:`Deploying on Google Cloud GKE <kubernetes-setup-gke>`
* :ref:`Deploying on Amazon EKS <kubernetes-setup-eks>`
* :ref:`Deploying on on-prem clusters <kubernetes-setup-onprem>`

.. _kubernetes-setup-kind:


Deploying locally on your laptop
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To try out SkyPilot on Kubernetes on your laptop or run SkyPilot
tasks locally without requiring any cloud access, we provide the
:code:`sky local up` CLI to create a 1-node Kubernetes cluster locally.

Under the hood, :code:`sky local up` uses `kind <https://kind.sigs.k8s.io/>`_,
a tool for creating a Kubernetes cluster on your local machine.
It runs a Kubernetes cluster inside a container, so no setup is required.

1. Install `Docker <https://docs.docker.com/engine/install/>`_ and `kind <https://kind.sigs.k8s.io/>`_.
2. Run :code:`sky local up` to launch a Kubernetes cluster and automatically configure your kubeconfig file:

   .. code-block:: console

     $ sky local up

3. Run :code:`sky check` and verify that Kubernetes is enabled in SkyPilot. You can now run SkyPilot tasks on this locally hosted Kubernetes cluster using :code:`sky launch`.
4. After you are done using the cluster, you can remove it with :code:`sky local down`. This will terminate the KinD container and switch your kubeconfig back to it's original context:

   .. code-block:: console

     $ sky local down

.. note::
    We recommend allocating at least 4 or more CPUs to your docker runtime to
    ensure kind has enough resources. See instructions
    `here <https://docs.docker.com/desktop/settings/linux/>`_.

.. note::
    kind does not support multiple nodes and GPUs.
    It is not recommended for use in a production environment.
    If you want to run a private on-prem cluster, see the section on :ref:`on-prem deployment <kubernetes-setup-onprem>` for more.


.. _kubernetes-setup-gke:

Deploying on Google Cloud GKE
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Create a GKE standard cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.
2. Get the kubeconfig for your cluster. The following command will automatically update ``~/.kube/config`` with new kubecontext for the GKE cluster:

   .. code-block:: console

     $ gcloud container clusters get-credentials <cluster-name> --region <region>

     # Example:
     # gcloud container clusters get-credentials testcluster --region us-central1-c

3. [If using GPUs] If your GKE nodes have GPUs, you may need to to
   `manually install <https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/>`_
   nvidia drivers. You can do so by deploying the daemonset
   depending on the GPU and OS on your nodes:

   .. code-block:: console

     # For Container Optimized OS (COS) based nodes with GPUs other than Nvidia L4 (e.g., V100, A100, ...):
     $ kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml

     # For Container Optimized OS (COS) based nodes with L4 GPUs:
     $ kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded-latest.yaml

     # For Ubuntu based nodes with GPUs other than Nvidia L4 (e.g., V100, A100, ...):
     $ kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/ubuntu/daemonset-preloaded.yaml

     # For Ubuntu based nodes with L4 GPUs:
     $ kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/ubuntu/daemonset-preloaded-R525.yaml

   To verify if GPU drivers are set up, run ``kubectl describe nodes`` and verify that ``nvidia.com/gpu`` is listed under the ``Capacity`` section.

4. Verify your kubeconfig (and GPU support, if available) is correctly set up by running :code:`sky check`:

   .. code-block:: console

     $ sky check

.. note::
    GKE autopilot clusters are currently not supported. Only GKE standard clusters are supported.


.. _kubernetes-setup-eks:

Deploying on Amazon EKS
^^^^^^^^^^^^^^^^^^^^^^^

1. Create a EKS cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.

2. Get the kubeconfig for your cluster. The following command will automatically update ``~/.kube/config`` with new kubecontext for the EKS cluster:

   .. code-block:: console

     $ aws eks update-kubeconfig --name <cluster-name> --region <region>

     # Example:
     # aws eks update-kubeconfig --name testcluster --region us-west-2

3. [If using GPUs] EKS clusters already come with Nvidia drivers set up. However, you will need to label the nodes with the GPU type. Use the SkyPilot node labelling tool to do so:

   .. code-block:: console

     python -m sky.utils.kubernetes.gpu_labeler


   This will create a job on each node to read the GPU type from `nvidia-smi` and assign a ``skypilot.co/accelerator`` label to the node. You can check the status of these jobs by running:

   .. code-block:: console

     kubectl get jobs -n kube-system

4. Verify your kubeconfig (and GPU support, if available) is correctly set up by running :code:`sky check`:

   .. code-block:: console

     $ sky check


.. _kubernetes-setup-onprem:

Deploying on on-prem clusters
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can also deploy Kubernetes on your on-prem clusters using off-the-shelf tools,
such as `kubeadm <https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/>`_,
`k3s <https://docs.k3s.io/quick-start>`_ or
`Rancher <https://ranchermanager.docs.rancher.com/v2.5/pages-for-subheaders/kubernetes-clusters-in-rancher-setup>`_.
Please follow their respective guides to deploy your Kubernetes cluster.

Setting up GPU support
~~~~~~~~~~~~~~~~~~~~~~
If your Kubernetes cluster has Nvidia GPUs, ensure that:

1. The Nvidia GPU operator is installed (i.e., ``nvidia.com/gpu`` resource is available on each node) and ``nvidia`` is set as the default runtime for your container engine. See `Nvidia's installation guide <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#install-nvidia-gpu-operator>`_ for more details.
2. Each node in your cluster is labelled with the GPU type. This labelling can be done by adding a label of the format ``skypilot.co/accelerators: <gpu_name>``, where the ``<gpu_name>`` is the lowercase name of the GPU. For example, a node with V100 GPUs must have a label :code:`skypilot.co/accelerators: v100`.

.. tip::
    You can check if GPU operator is installed and the ``nvidia`` runtime is set as default by running:

    .. code-block:: console

        $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/gpu_test_pod.yaml
        $ watch kubectl get pods
        # If the pod status changes to completed after a few minutes, your Kubernetes environment is set up correctly.


.. note::
    If you are using RKE2, the GPU operator installation through helm requires extra flags to set ``nvidia`` as the default runtime for containerd. Refer to instructions on `Nvidia GPU Operator installation with Helm on RKE2 <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#custom-configuration-for-runtime-containerd>`_ for details.

We provide a convenience script that automatically detects GPU types and labels each node. You can run it with:

.. code-block:: console

 $ python -m sky.utils.kubernetes.gpu_labeler

 Created GPU labeler job for node ip-192-168-54-76.us-west-2.compute.internal
 Created GPU labeler job for node ip-192-168-93-215.us-west-2.compute.internal
 GPU labeling started - this may take a few minutes to complete.
 To check the status of GPU labeling jobs, run `kubectl get jobs --namespace=kube-system -l job=sky-gpu-labeler`
 You can check if nodes have been labeled by running `kubectl describe nodes` and looking for labels of the format `skypilot.co/accelerators: <gpu_name>`.


.. note::
 GPU labels are case-sensitive. Ensure that the GPU name is lowercase if you are using the ``skypilot.co/accelerators`` label.

.. note::
 GPU labelling is not required on GKE clusters - SkyPilot will automatically use GKE provided labels. However, you will still need to install `drivers <https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers>`_.


.. note::
 If the GPU labelling process fails, you can run ``python -m sky.utils.kubernetes.gpu_labeler --cleanup`` to clean up the failed jobs.

Once the cluster is deployed and you have placed your kubeconfig at ``~/.kube/config``, verify your setup by running :code:`sky check`:

.. code-block:: console

    $ sky check


.. _kubernetes-observability:

Observability for Administrators
--------------------------------
All SkyPilot tasks are run in pods inside a Kubernetes cluster. As a cluster administrator,
you can inspect running pods (e.g., with :code:`kubectl get pods -n namespace`) to check which
tasks are running and how many resources they are consuming on the cluster.

Additionally, you can also deploy tools such as the `Kubernetes dashboard <https://kubernetes.io/docs/tasks/access-application-cluster/web-ui-dashboard/>`_ for easily viewing and managing
SkyPilot tasks running on your cluster.

.. image:: ../../images/screenshots/kubernetes/kubernetes-dashboard.png
    :width: 80%
    :align: center
    :alt: Kubernetes Dashboard


As a demo, we provide a sample Kubernetes dashboard deployment manifest that you can deploy with:

.. code-block:: console

    $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/scripts/dashboard.yaml


To access the dashboard, run:

.. code-block:: console

    $ kubectl proxy


In a browser, open http://localhost:8001/api/v1/namespaces/kubernetes-dashboard/services/https:kubernetes-dashboard:/proxy/ and click on Skip when
prompted for credentials.

Note that this dashboard can only be accessed from the machine where the ``kubectl proxy`` command is executed.

.. note::
    The demo dashboard is not secure and should not be used in production. Please refer to the
    `Kubernetes documentation <https://kubernetes.io/docs/tasks/access-application-cluster/web-ui-dashboard/>`_
    for more information on how to set up access control for the dashboard.


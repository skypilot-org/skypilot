.. _kubernetes-deployment:

Deployment Guides
-----------------
Below we include minimal guides to set up a new Kubernetes cluster in different environments, including hosted services on the cloud.

.. grid:: 2
    :gutter: 3

    .. grid-item-card::  Local Development Cluster
        :link: kubernetes-setup-kind
        :link-type: ref
        :text-align: center

        Run a local Kubernetes cluster on your laptop with ``sky local up``.

    .. grid-item-card::  On-prem Clusters (RKE2, K3s, etc.)
        :link: kubernetes-setup-onprem
        :link-type: ref
        :text-align: center

        For on-prem deployments with kubeadm, RKE2, K3s or other distributions.

    .. grid-item-card:: Google Cloud - GKE
        :link: kubernetes-setup-gke
        :link-type: ref
        :text-align: center

        Google's hosted Kubernetes service.

    .. grid-item-card::  Amazon - EKS
        :link: kubernetes-setup-eks
        :link-type: ref
        :text-align: center

        Amazon's hosted Kubernetes service.

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


.. _kubernetes-setup-onprem-distro-specific:

Notes for specific Kubernetes distributions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some Kubernetes distributions require additional steps to set up GPU support.

Rancher Kubernetes Engine 2 (RKE2)
**********************************

Nvidia GPU operator installation on RKE2 through helm requires extra flags to set ``nvidia`` as the default runtime for containerd.

.. code-block:: console

    $ helm install gpu-operator -n gpu-operator --create-namespace \
      nvidia/gpu-operator $HELM_OPTIONS \
        --set 'toolkit.env[0].name=CONTAINERD_CONFIG' \
        --set 'toolkit.env[0].value=/var/lib/rancher/rke2/agent/etc/containerd/config.toml.tmpl' \
        --set 'toolkit.env[1].name=CONTAINERD_SOCKET' \
        --set 'toolkit.env[1].value=/run/k3s/containerd/containerd.sock' \
        --set 'toolkit.env[2].name=CONTAINERD_RUNTIME_CLASS' \
        --set 'toolkit.env[2].value=nvidia' \
        --set 'toolkit.env[3].name=CONTAINERD_SET_AS_DEFAULT' \
        --set-string 'toolkit.env[3].value=true'

Refer to instructions on `Nvidia GPU Operator installation with Helm on RKE2 <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#rancher-kubernetes-engine-2>`_ for details.

K3s
***

Installing Nvidia GPU operator on K3s is similar to `RKE2 instructions from Nvidia <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#rancher-kubernetes-engine-2>`_, but requires changing
the ``CONTAINERD_CONFIG`` variable to ``/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl``. Here is an example command to install the Nvidia GPU operator on K3s:

.. code-block:: console

    $ helm install gpu-operator -n gpu-operator --create-namespace \
      nvidia/gpu-operator $HELM_OPTIONS \
        --set 'toolkit.env[0].name=CONTAINERD_CONFIG' \
        --set 'toolkit.env[0].value=/var/lib/rancher/k3s/agent/etc/containerd/config.toml' \
        --set 'toolkit.env[1].name=CONTAINERD_SOCKET' \
        --set 'toolkit.env[1].value=/run/k3s/containerd/containerd.sock' \
        --set 'toolkit.env[2].name=CONTAINERD_RUNTIME_CLASS' \
        --set 'toolkit.env[2].value=nvidia'

Check the status of the GPU operator installation by running ``kubectl get pods -n gpu-operator``. It takes a few minutes to install and some CrashLoopBackOff errors are expected during the installation process.

.. tip::

    If your gpu-operator installation stays stuck in CrashLoopBackOff, you may need to create a symlink to the ``ldconfig`` binary to work around a `known issue <https://github.com/NVIDIA/nvidia-docker/issues/614#issuecomment-423991632>`_ with nvidia-docker runtime. Run the following command on your nodes:

    .. code-block:: console

        $ ln -s /sbin/ldconfig /sbin/ldconfig.real

After the GPU operator is installed, create the nvidia RuntimeClass required by K3s. This runtime class will automatically be used by SkyPilot to schedule GPU pods:

.. code-block:: console

    $ kubectl apply -f - <<EOF
    apiVersion: node.k8s.io/v1
    kind: RuntimeClass
    metadata:
      name: nvidia
    handler: nvidia
    EOF
.. _kubernetes-deployment:

Deployment Guides
=================

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

    .. grid-item-card::  On-demand Cloud VMs
        :link: kubernetes-setup-ondemand
        :link-type: ref
        :text-align: center

        We provide scripts to deploy k8s on on-demand cloud VMs.

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
4. After you are done using the cluster, you can remove it with :code:`sky local down`. This will destroy the local kubernetes cluster and switch your kubeconfig back to it's original context:

   .. code-block:: console

     $ sky local down

.. note::
    We recommend allocating at least 4 or more CPUs to your docker runtime to
    ensure kind has enough resources. See instructions to increase CPU allocation
    `here <https://kind.sigs.k8s.io/docs/user/known-issues/#failure-to-build-node-image>`_.

.. note::
    kind does not support multiple nodes and GPUs.
    It is not recommended for use in a production environment.
    If you want to run a private on-prem cluster, see the section on :ref:`on-prem deployment <kubernetes-setup-onprem>` for more.


.. _kubernetes-setup-gke:

Deploying on Google Cloud GKE
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Create a GKE standard cluster with at least 1 node. We recommend creating nodes with at least 4 vCPUs.

   .. raw:: HTML

       <details>

       <summary>Example: create a GKE cluster with 2 nodes, each having 16 CPUs.</summary>

   .. code-block:: bash

       PROJECT_ID=$(gcloud config get-value project)
       CLUSTER_NAME=testcluster
       gcloud beta container --project "${PROJECT_ID}" clusters create "${CLUSTER_NAME}" --zone "us-central1-c" --no-enable-basic-auth --cluster-version "1.29.4-gke.1043002" --release-channel "regular" --machine-type "e2-standard-16" --image-type "COS_CONTAINERD" --disk-type "pd-balanced" --disk-size "100" --metadata disable-legacy-endpoints=true --scopes "https://www.googleapis.com/auth/devstorage.read_only","https://www.googleapis.com/auth/logging.write","https://www.googleapis.com/auth/monitoring","https://www.googleapis.com/auth/servicecontrol","https://www.googleapis.com/auth/service.management.readonly","https://www.googleapis.com/auth/trace.append" --num-nodes "2" --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-ip-alias --network "projects/${PROJECT_ID}/global/networks/default" --subnetwork "projects/${PROJECT_ID}/regions/us-central1/subnetworks/default" --no-enable-intra-node-visibility --default-max-pods-per-node "110" --security-posture=standard --workload-vulnerability-scanning=disabled --no-enable-master-authorized-networks --addons HorizontalPodAutoscaling,HttpLoadBalancing,GcePersistentDiskCsiDriver --enable-autoupgrade --enable-autorepair --max-surge-upgrade 1 --max-unavailable-upgrade 0 --enable-managed-prometheus --enable-shielded-nodes --node-locations "us-central1-c"

   .. raw:: html

       </details>


2. Get the kubeconfig for your cluster. The following command will automatically update ``~/.kube/config`` with new kubecontext for the GKE cluster:

   .. code-block:: console

     $ gcloud container clusters get-credentials <cluster-name> --region <region>

     # Example:
     # gcloud container clusters get-credentials testcluster --region us-central1-c

3. [If using GPUs] For GKE versions newer than 1.30.1-gke.115600, NVIDIA drivers are pre-installed and no additional setup is required. If you are using an older GKE version, you may need to
   `manually install <https://cloud.google.com/kubernetes-engine/docs/how-to/gpus#installing_drivers>`_
   NVIDIA drivers for GPU support. You can do so by deploying the daemonset
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

   .. tip::
      To verify if GPU drivers are set up, run ``kubectl describe nodes`` and verify that ``nvidia.com/gpu`` resource is listed under the ``Capacity`` section.

4. Verify your kubernetes cluster is correctly set up for SkyPilot by running :code:`sky check`:

   .. code-block:: console

     $ sky check

5. [If using GPUs] Check available GPUs in the kubernetes cluster with :code:`sky show-gpus --infra k8s`

   .. code-block:: console

       $ sky show-gpus --infra k8s
       GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION
       L4    1, 2, 4                   6 of 8 free
       A100  1, 2                      2 of 4 free

       Kubernetes per node GPU availability
       NODE                       GPU       UTILIZATION
       my-cluster-0               L4        4 of 4 free
       my-cluster-1               L4        2 of 4 free
       my-cluster-2               A100      2 of 2 free
       my-cluster-3               A100      0 of 2 free

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

4. Verify your kubernetes cluster is correctly set up for SkyPilot by running :code:`sky check`:

   .. code-block:: console

     $ sky check

5. [If using GPUs] Check available GPUs in the kubernetes cluster with :code:`sky show-gpus --infra k8s`

   .. code-block:: console

       $ sky show-gpus --infra k8s
       GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION
       A100  1, 2                      2 of 2 free

       Kubernetes per node GPU availability
       NODE                       GPU       UTILIZATION
       my-cluster-0               A100      2 of 2 free

.. _kubernetes-setup-onprem:

Deploying on on-prem clusters
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you have a list of IP addresses and the SSH credentials for your on-prem cluster, you can follow our
:ref:`Using Existing Machines <existing-machines>` guide to set up SkyPilot on your on-prem cluster.

Alternatively, you can also deploy Kubernetes on your on-prem clusters using off-the-shelf tools,
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


.. _kubernetes-setup-ondemand:

Deploying on cloud VMs
^^^^^^^^^^^^^^^^^^^^^^

You can also spin up on-demand cloud VMs and deploy Kubernetes on them.

We provide scripts to take care of provisioning VMs, installing Kubernetes, setting up GPU support and configuring your local kubeconfig.
Refer to our `Deploying Kubernetes on VMs guide <https://github.com/skypilot-org/skypilot/tree/master/examples/k8s_cloud_deploy>`_ for more details.

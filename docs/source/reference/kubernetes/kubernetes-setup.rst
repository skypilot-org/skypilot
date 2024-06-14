.. _kubernetes-setup:

Kubernetes Cluster Setup
========================


.. note::
    This is a guide for cluster administrators on how to set up Kubernetes clusters
    for use with SkyPilot.

    If you are a SkyPilot user and your cluster administrator has already set up a cluster
    and shared a kubeconfig file with you, :ref:`Submitting tasks to Kubernetes <kubernetes-instructions>`
    explains how to submit tasks to your cluster.

.. grid:: 1 1 3 3
    :gutter: 2

    .. grid-item-card:: ⚙️ Setup Kubernetes Cluster
        :link: kubernetes-setup-intro
        :link-type: ref
        :text-align: center

        Configure your Kubernetes cluster to run SkyPilot.

    .. grid-item-card::  ✅️ Verify Setup
        :link: kubernetes-setup-verify
        :link-type: ref
        :text-align: center

        Ensure your cluster is set up correctly for SkyPilot.


    .. grid-item-card::  👀️ Observability
        :link: kubernetes-observability
        :link-type: ref
        :text-align: center

        Use your existing Kubernetes tooling to monitor SkyPilot resources.



.. _kubernetes-setup-intro:

Setting up Kubernetes cluster for SkyPilot
------------------------------------------

To prepare a Kubernetes cluster to run SkyPilot, the cluster administrator must:

1. :ref:`Deploy a cluster <kubernetes-setup-deploy>` running Kubernetes v1.20 or later.
2. Set up :ref:`GPU support <kubernetes-setup-gpusupport>`.
3. [Optional] :ref:`Set up ports <kubernetes-setup-ports>` for exposing services.
4. [Optional] :ref:`Set up permissions <kubernetes-setup-serviceaccount>`: create a namespace for your users and/or create a service account with minimal permissions for SkyPilot.

After these steps, the administrator can share the kubeconfig file with users, who can then submit tasks to the cluster using SkyPilot.

.. _kubernetes-setup-deploy:

Step 1 - Deploy a Kubernetes Cluster
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. tip::

    If you already have a Kubernetes cluster, skip this step.

Below we link to minimal guides to set up a new Kubernetes cluster in different environments, including hosted services on the cloud.

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


.. _kubernetes-setup-gpusupport:

Step 2 - Set up GPU support
^^^^^^^^^^^^^^^^^^^^^^^^^^^

To utilize GPUs on Kubernetes, your cluster must:

1. Have the ``nvidia.com/gpu`` **resource** available on all GPU nodes and have ``nvidia`` as the default runtime for your container engine.

   * If you are following :ref:`our deployment guides <kubernetes-deployment>` or using GKE or EKS, this would already be set up. Else, install the `Nvidia GPU Operator <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#install-nvidia-gpu-operator>`_.

2. Have a **label on each node specifying the GPU type**. See :ref:`Setting up GPU labels <kubernetes-gpu-labels>` for more details.


.. tip::
    To verify the `Nvidia GPU Operator <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#install-nvidia-gpu-operator>`_ is installed after step 1 and the ``nvidia`` runtime is set as default, run:

    .. code-block:: console

        $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/gpu_test_pod.yaml
        $ watch kubectl get pods
        # If the pod status changes to completed after a few minutes, Nvidia GPU driver is set up correctly. Move on to setting up GPU labels.

.. note::

    Refer to :ref:`Notes for specific Kubernetes distributions <kubernetes-setup-onprem-distro-specific>` for additional instructions on setting up GPU support on specific Kubernetes distributions, such as RKE2 and K3s.


.. _kubernetes-gpu-labels:

Setting up GPU labels
~~~~~~~~~~~~~~~~~~~~~

.. tip::

    If your cluster has the Nvidia GPU Operator installed or you are using GKE or Karpenter, your cluster already has the necessary GPU labels. You can skip this section.

To use GPUs with SkyPilot, cluster nodes must be labelled with the GPU type. This informs SkyPilot which GPU types are available on the cluster.

Currently supported labels are:

* ``nvidia.com/gpu.product``: automatically created by Nvidia GPU Operator.
* ``cloud.google.com/gke-accelerator``: used by GKE clusters.
* ``karpenter.k8s.aws/instance-gpu-name``: used by Karpenter.
* ``skypilot.co/accelerator``: custom label used by SkyPilot if none of the above are present.

Any one of these labels is sufficient for SkyPilot to detect GPUs on the cluster.

.. tip::

    To check if your nodes contain the necessary labels, run:

    .. code-block:: bash

        output=$(kubectl get nodes --show-labels | awk -F'[, ]' '{for (i=1; i<=NF; i++) if ($i ~ /nvidia.com\/gpu.product=|cloud.google.com\/gke-accelerator=|karpenter.k8s.aws\/instance-gpu-name=|skypilot.co\/accelerator=/) print $i}')
        if [ -z "$output" ]; then
          echo "No valid GPU labels found."
        else
          echo "GPU Labels found:"
          echo "$output"
        fi

Automatically Labelling Nodes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If none of the above labels are present on your cluster, we provide a convenience script that automatically detects GPU types and labels each node with the ``skypilot.co/accelerator`` label. You can run it with:

.. code-block:: console

 $ python -m sky.utils.kubernetes.gpu_labeler

 Created GPU labeler job for node ip-192-168-54-76.us-west-2.compute.internal
 Created GPU labeler job for node ip-192-168-93-215.us-west-2.compute.internal
 GPU labeling started - this may take 10 min or more to complete.
 To check the status of GPU labeling jobs, run `kubectl get jobs --namespace=kube-system -l job=sky-gpu-labeler`
 You can check if nodes have been labeled by running `kubectl describe nodes` and looking for labels of the format `skypilot.co/accelerator: <gpu_name>`.

.. note::

    If the GPU labelling process fails, you can run ``python -m sky.utils.kubernetes.gpu_labeler --cleanup`` to clean up the failed jobs.

Manually Labelling Nodes
~~~~~~~~~~~~~~~~~~~~~~~~

You can also manually label nodes, if required. Labels must be of the format ``skypilot.co/accelerator: <gpu_name>`` where ``<gpu_name>`` is the lowercase name of the GPU.

For example, a node with V100 GPUs must have a label :code:`skypilot.co/accelerator: v100`.

Use the following command to label a node:

.. code-block:: bash

    kubectl label nodes <node-name> skypilot.co/accelerator=<gpu_name>


.. note::

    GPU labels are case-sensitive. Ensure that the GPU name is lowercase if you are using the ``skypilot.co/accelerator`` label.


.. _kubernetes-setup-ports:

[Optional] Step 3 - Set up for Exposing Services
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. tip::

    If you are using GKE or EKS or do not plan expose ports publicly on Kubernetes (such as ``sky launch --ports``, SkyServe), no additional setup is required. On GKE and EKS, SkyPilot will create a LoadBalancer service automatically.

Running SkyServe or tasks exposing ports requires additional setup to expose ports running services.
SkyPilot supports either of two modes to expose ports:

* :ref:`LoadBalancer Service <kubernetes-loadbalancer>` (default)
* :ref:`Nginx Ingress <kubernetes-ingress>`

Refer to :ref:`Exposing Services on Kubernetes <kubernetes-ports>` for more details.

.. _kubernetes-setup-serviceaccount:

[Optional] Step 4 - Namespace and Service Account Setup
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. tip::

    This step is optional and required only in specific environments. By default, SkyPilot runs in the namespace configured in current `kube-context <https://kubernetes.io/docs/tasks/access-application-cluster/configure-access-multiple-clusters/#define-clusters-users-and-contexts>`_ and creates a service account named ``skypilot-service-account`` to run tasks.
    **This step is not required if you use these defaults.**

If your cluster requires isolating SkyPilot tasks to a specific namespace and restricting the permissions granted to users,
you can create a new namespace and service account for SkyPilot to use.

The minimal permissions required for the service account can be found on the :ref:`Minimal Kubernetes Permissions <cloud-permissions-kubernetes>` page.

To simplify the setup, we provide a `script <https://github.com/skypilot-org/skypilot/blob/master/sky/utils/kubernetes/generate_kubeconfig.sh>`_ that creates a namespace and service account with the necessary permissions for a given service account name and namespace.

.. code-block:: bash

    # Download the script
    wget https://raw.githubusercontent.com/skypilot-org/skypilot/master/sky/utils/kubernetes/generate_kubeconfig.sh
    chmod +x generate_kubeconfig.sh

    # Execute the script to generate a kubeconfig file with the service account and namespace
    # Replace my-sa and my-namespace with your desired service account name and namespace
    # The script will create the namespace if it does not exist and create a service account with the necessary permissions.
    SKYPILOT_SA_NAME=my-sa SKYPILOT_NAMESPACE=my-namespace ./generate_kubeconfig.sh

You may distribute the generated kubeconfig file to users who can then use it to submit tasks to the cluster.

.. _kubernetes-setup-verify:

Verifying Setup
---------------

Once the cluster is deployed and you have placed your kubeconfig at ``~/.kube/config``, verify your setup by running :code:`sky check`:

.. code-block:: bash

    sky check kubernetes

This should show ``Kubernetes: Enabled`` without any warnings.

You can also check the GPUs available on your nodes by running:

.. code-block:: console

    $ sky show-gpus --cloud kubernetes
    GPU   QTY_PER_NODE  TOTAL_GPUS  TOTAL_FREE_GPUS
    L4    1, 2, 3, 4    8           6
    H100  1, 2          4           2


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


Troubleshooting Kubernetes Setup
--------------------------------

If you encounter issues while setting up your Kubernetes cluster, please refer to the :ref:`troubleshooting guide <kubernetes-troubleshooting>` to diagnose and fix issues.


.. toctree::
   :hidden:

   kubernetes-deployment
   Exposing Services <kubernetes-ports>

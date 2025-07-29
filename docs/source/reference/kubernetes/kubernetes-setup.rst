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

After these required steps, perform optional setup steps as needed:

* :ref:`kubernetes-setup-volumes`
* :ref:`kubernetes-setup-priority`
* :ref:`kubernetes-setup-serviceaccount`
* :ref:`kubernetes-setup-ports`

Once completed, the administrator can share the kubeconfig file with users, who can then submit tasks to the cluster using SkyPilot.

.. _kubernetes-setup-deploy:

Step 1 - Deploy a Kubernetes cluster
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

-  If using NVIDIA GPUs, have the ``nvidia.com/gpu`` **resource** available on all GPU nodes and have ``nvidia`` as the default runtime for your container engine.

   * If you are following :ref:`our deployment guides <kubernetes-deployment>` or using GKE or EKS, this would already be set up. Else, install the `Nvidia GPU Operator <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#install-nvidia-gpu-operator>`_.

- If using AMD GPUs, have the ``amd.com/gpu`` **resource** available on all GPU nodes and install the AMD GPU Operator.

  * Follow the instructions in :ref:`AMD GPUs on Kubernetes <kubernetes-amd-gpu>` to install the AMD GPU Operator.

- Have a **label on each node specifying the GPU type**. See :ref:`Setting up GPU labels <kubernetes-gpu-labels>` for more details.


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

Automatically labelling nodes
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

    Automatically labelling AMD GPUs is not supported at this moment. Please follow the instructions in "Manually labelling nodes" section below.

.. note::

    If the GPU labelling process fails, you can run ``python -m sky.utils.kubernetes.gpu_labeler --cleanup`` to clean up the failed jobs.

Manually labelling nodes
~~~~~~~~~~~~~~~~~~~~~~~~

You can also manually label nodes, if required. Labels must be of the format ``skypilot.co/accelerator: <gpu_name>`` where ``<gpu_name>`` is the lowercase name of the GPU.

For example, a node with H100 GPUs must have a label :code:`skypilot.co/accelerator: h100`, and a node with MI300 GPUs must have a label :code:`skypilot.co/accelerator: mi300`.

Use the following command to label a node:

.. code-block:: bash

    kubectl label nodes <node-name> skypilot.co/accelerator=<gpu_name>


.. note::

    GPU labels are case-sensitive. Ensure that the GPU name is lowercase if you are using the ``skypilot.co/accelerator`` label.

.. _kubernetes-setup-verify:

Verifying setup
---------------

Once the cluster is deployed and you have placed your kubeconfig at ``~/.kube/config``, verify your setup by running :code:`sky check`:

.. code-block:: bash

    sky check kubernetes

This should show ``Kubernetes: Enabled`` without any warnings.

You can also check the GPUs available on your nodes by running:

.. code-block:: console

    $ sky show-gpus --infra k8s
    Kubernetes GPUs
    GPU   REQUESTABLE_QTY_PER_NODE  UTILIZATION
    L4    1, 2, 4                   12 of 12 free
    H100  1, 2, 4, 8                16 of 16 free

    Kubernetes per node GPU availability
    NODE                       GPU       UTILIZATION
    my-cluster-0               L4        4 of 4 free
    my-cluster-1               L4        4 of 4 free
    my-cluster-2               L4        2 of 2 free
    my-cluster-3               L4        2 of 2 free
    my-cluster-4               H100      8 of 8 free
    my-cluster-5               H100      8 of 8 free

.. _kubernetes-optional-steps:

Optional setup
--------------

The following setup steps are optional and can be performed based on your specific requirements:

* :ref:`kubernetes-setup-volumes`
* :ref:`kubernetes-setup-priority`
* :ref:`kubernetes-setup-serviceaccount`
* :ref:`kubernetes-setup-ports`
* :ref:`kubernetes-setup-fuse`

.. _kubernetes-setup-volumes:

Set up NFS and other volumes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

`Kubernetes volumes <https://kubernetes.io/docs/concepts/storage/volumes/>`_ can be attached to your SkyPilot pods using the :ref:`pod_config <kubernetes-custom-pod-config>` field. This is useful for accessing shared storage such as NFS or local high-performance storage like NVMe drives.

Volume mounting can be done directly in the task YAML on a per-task basis, or globally for all tasks in :code:`~/.sky/config.yaml`.

Examples:

.. tab-set::

    .. tab-item:: NFS using hostPath
      :name: kubernetes-volumes-hostpath-nfs

      Mount a NFS share that's `already mounted on the Kubernetes nodes <https://kubernetes.io/docs/concepts/storage/volumes/#hostpath>`_.

      **Per-task configuration:**

      .. code-block:: yaml

           # task.yaml
           run: |
             echo "Hello, world!" > /mnt/nfs/hello.txt
             ls -la /mnt/nfs

           config:
             kubernetes:
               pod_config:
                 spec:
                   containers:
                     - volumeMounts:
                         - mountPath: /mnt/nfs
                           name: my-host-nfs
                   volumes:
                     - name: my-host-nfs
                       hostPath:
                         path: /path/on/host/nfs
                         type: Directory

      **Global configuration:**

      .. code-block:: yaml

           # ~/.sky/config.yaml
           kubernetes:
             pod_config:
               spec:
                 containers:
                   - volumeMounts:
                       - mountPath: /mnt/nfs
                         name: my-host-nfs
                 volumes:
                   - name: my-host-nfs
                     hostPath:
                       path: /path/on/host/nfs
                       type: Directory

    .. tab-item:: NFS using native volume
      :name: kubernetes-volumes-native-nfs

      Mount a NFS share using Kubernetes' `native NFS volume <https://kubernetes.io/docs/concepts/storage/volumes/#nfs>`_ support.

      **Per-task configuration:**

      .. code-block:: yaml

           # task.yaml
           run: |
             echo "Hello, world!" > /mnt/nfs/hello.txt
             ls -la /mnt/nfs

           config:
             kubernetes:
               pod_config:
                 spec:
                    containers:
                      - volumeMounts:
                          - mountPath: /mnt/nfs
                            name: nfs-volume
                    volumes:
                      - name: nfs-volume
                        nfs:
                          server: nfs.example.com
                          path: /shared
                          readOnly: false

      **Global configuration:**

      .. code-block:: yaml

           # ~/.sky/config.yaml
           kubernetes:
             pod_config:
               spec:
                 containers:
                   - volumeMounts:
                       - mountPath: /mnt/nfs
                         name: nfs-volume
                 volumes:
                   - name: nfs-volume
                     nfs:
                       server: nfs.example.com
                       path: /shared
                       readOnly: false

    .. tab-item:: NVMe using hostPath
      :name: kubernetes-volumes-hostpath-nvme

      Mount local NVMe storage that's already mounted on the Kubernetes nodes.

      **Per-task configuration:**

      .. code-block:: yaml

           # task.yaml
           run: |
             echo "Hello, world!" > /mnt/nvme/hello.txt
             ls -la /mnt/nvme

           config:
             kubernetes:
               pod_config:
                 spec:
                    containers:
                      - volumeMounts:
                          - mountPath: /mnt/nvme
                            name: nvme
                    volumes:
                      - name: nvme
                        hostPath:
                          path: /path/on/host/nvme
                          type: Directory

      **Global configuration:**

      .. code-block:: yaml

           # ~/.sky/config.yaml
           kubernetes:
             pod_config:
               spec:
                 containers:
                   - volumeMounts:
                       - mountPath: /mnt/nvme
                         name: nvme
                 volumes:
                   - name: nvme
                     hostPath:
                       path: /path/on/host/nvme
                       type: Directory

    .. tab-item:: Nebius shared filesystem
      :name: kubernetes-volumes-nebius-shared-filesystem

      When creating a node group on the Nebius console, attach your desired shared file system to the node group (``Create Node Group`` -> ``Attach shared filesystem``):

      * Ensure ``Auto mount`` is enabled.
      * Note the ``Mount tag`` (e.g. ``filesystem-d0``).

      .. image:: ../../images/screenshots/nebius/nebius-k8s-attach-fs.png
        :width: 50%
        :align: center

      Nebius will automatically mount the shared filesystem to hosts in the node group. You can then use a ``hostPath`` volume to mount the shared filesystem to your SkyPilot pods.

      **Per-task configuration:**

      .. code-block:: yaml

           # task.yaml
           run: |
             echo "Hello, world!" > /mnt/nfs/hello.txt
             ls -la /mnt/nfs

           config:
             kubernetes:
               pod_config:
                 spec:
                   containers:
                     - volumeMounts:
                         - mountPath: /mnt/nfs
                           name: nebius-sharedfs
                   volumes:
                     - name: nebius-sharedfs
                       hostPath:
                         path: /mnt/<mount_tag> # e.g. /mnt/filesystem-d0
                         type: Directory


.. note::

  When using `hostPath volumes <https://kubernetes.io/docs/concepts/storage/volumes/#hostpath>`_, the specified paths must already exist on the Kubernetes node where the pod is scheduled.

  For NFS mounts using hostPath, ensure the NFS mount is already configured on all Kubernetes nodes.


.. _kubernetes-setup-priority:

Set up priority and preemption
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default, all SkyPilot pods use the default Kubernetes priority class configured in your cluster. Pods will queue if there are no resources available.

To assign priorities to SkyPilot pods and enable preemption to prioritize critical jobs, refer to :ref:`kubernetes-priorities`.


.. _kubernetes-setup-serviceaccount:

Set up namespaces and service accounts
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. tip::

    By default, SkyPilot runs in the namespace configured in current `kube-context <https://kubernetes.io/docs/tasks/access-application-cluster/configure-access-multiple-clusters/#define-clusters-users-and-contexts>`_ and creates a service account named ``skypilot-service-account`` to run tasks.
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


.. _kubernetes-setup-ports:

Set up for exposing services
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. tip::

    If you are using GKE or EKS or do not plan expose ports publicly on Kubernetes (such as ``sky launch --ports``, SkyServe), no additional setup is required. On GKE and EKS, SkyPilot will create a LoadBalancer service automatically.

Running SkyServe or tasks exposing ports requires additional setup to expose ports running services.
SkyPilot supports either of two modes to expose ports:

* :ref:`LoadBalancer Service <kubernetes-loadbalancer>` (default)
* :ref:`Nginx Ingress <kubernetes-ingress>`

Refer to :ref:`Exposing Services on Kubernetes <kubernetes-ports>` for more details.

.. _kubernetes-setup-fuse:

Set up FUSE proxy
^^^^^^^^^^^^^^^^^

By default, SkyPilot automatically sets up a FUSE proxy to allow Pods created by SkyPilot to perform FUSE mounts/unmounts operations without root privileges. The proxy requires root privileges and ``SYS_ADMIN`` capabilities, which may require additional security audits.

In most clusters, SkyPilot handles setting up the FUSE proxy as a privileged DaemonSet, and **no manual configuration is required by the user**.

However, if you are operating in a cluster with restricted permissions, you can deploy the DaemonSet externally, to avoid the need to grant SkyPilot the permission to create privileged DaemonSets. SkyPilot will automatically discover the DaemonSet and use it as the FUSE proxy:

.. code-block:: console

    # If you do not want to grant SkyPilot the ability to create privileged daemonsets, manually deploy the FUSE proxy:
    $ kubectl create namespace skypilot-system || true
    $ kubectl -n skypilot-system apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/sky/provision/kubernetes/manifests/fusermount-server-daemonset.yaml

.. _kubernetes-observability:

Observability for administrators
--------------------------------
All SkyPilot tasks are run in pods inside a Kubernetes cluster. As a cluster administrator,
you can inspect running pods (e.g., with :code:`kubectl get pods -n namespace`) to check which
tasks are running and how many resources they are consuming on the cluster.

Below, we provide tips on how to monitor SkyPilot resources on your Kubernetes cluster.

.. _kubernetes-observability-skystatus:

List SkyPilot resources across all users
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

We provide a convenience command, :code:`sky status --k8s`, to view the status of all SkyPilot resources in the cluster.

Unlike :code:`sky status` which lists only the SkyPilot resources launched by the current user,
:code:`sky status --k8s` lists all SkyPilot resources in the cluster across all users.

.. code-block:: console

    $ sky status --k8s
    Kubernetes cluster state (context: mycluster)
    SkyPilot clusters
    USER     NAME                           LAUNCHED    RESOURCES                                  STATUS
    alice    infer-svc-1                    23 hrs ago  1x Kubernetes(cpus=1, mem=1, {'L4': 1})    UP
    alice    sky-jobs-controller-80b50983   2 days ago  1x Kubernetes(cpus=4, mem=4)               UP
    alice    sky-serve-controller-80b50983  23 hrs ago  1x Kubernetes(cpus=4, mem=4)               UP
    bob      dev                            1 day ago   1x Kubernetes(cpus=2, mem=8, {'H100': 1})  UP
    bob      multinode-dev                  1 day ago   2x Kubernetes(cpus=2, mem=2)               UP
    bob      sky-jobs-controller-2ea485ea   2 days ago  1x Kubernetes(cpus=4, mem=4)               UP

    Managed jobs
    In progress tasks: 1 STARTING
    USER     ID  TASK  NAME      RESOURCES   SUBMITTED   TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
    alice    1   -     eval      1x[CPU:1+]  2 days ago  49s            8s            0            SUCCEEDED
    bob      4   -     pretrain  1x[H100:4]  1 day ago   1h 1m 11s      1h 14s        0            SUCCEEDED
    bob      3   -     bigjob    1x[CPU:16]  1 day ago   1d 21h 11m 4s  -             0            STARTING
    bob      2   -     failjob   1x[CPU:1+]  1 day ago   54s            9s            0            FAILED
    bob      1   -     shortjob  1x[CPU:1+]  2 days ago  1h 1m 19s      1h 16s        0            SUCCEEDED


.. _kubernetes-observability-dashboard:

Kubernetes dashboard
^^^^^^^^^^^^^^^^^^^^
You can deploy tools such as the `Kubernetes dashboard <https://kubernetes.io/docs/tasks/access-application-cluster/web-ui-dashboard/>`_ to easily view and manage
SkyPilot resources on your cluster.

.. image:: ../../images/screenshots/kubernetes/kubernetes-dashboard.png
    :width: 80%
    :align: center
    :alt: Kubernetes dashboard


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


Troubleshooting Kubernetes setup
--------------------------------

If you encounter issues while setting up your Kubernetes cluster, please refer to the :ref:`troubleshooting guide <kubernetes-troubleshooting>` to diagnose and fix issues.


.. toctree::
   :hidden:

   kubernetes-deployment
   Exposing Services <kubernetes-ports>

.. _kubernetes-getting-started:

Getting Started on Kubernetes
=============================

Quickstart
----------
Have a kubeconfig? Get started with SkyPilot in 3 commands:

.. code-block:: bash

   # Install dependencies
   $ brew install kubectl socat netcat
   # Linux: sudo apt-get install kubectl socat netcat

   # With a valid kubeconfig at ~/.kube/config, run:
   $ sky check
   # Shows "Kubernetes: enabled"

   # Launch your SkyPilot cluster
   $ sky launch --cpus 2+ -- echo hi

For detailed instructions, prerequisites, and advanced features, read on.

Prerequisites
-------------

To connect and use a Kubernetes cluster, SkyPilot needs:

* An existing Kubernetes cluster running Kubernetes v1.20 or later.
* A `Kubeconfig <https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/>`_ file containing access credentials and namespace to be used.

**Supported Kubernetes deployments:**

* Hosted Kubernetes services (EKS, GKE)
* On-prem clusters (Kubeadm, Rancher, K3s)
* Local development clusters (KinD, minikube)

In a typical workflow:

1. A cluster administrator sets up a Kubernetes cluster. Refer to admin guides for
   :ref:`Kubernetes cluster setup <kubernetes-setup>` for different deployment environments (Amazon EKS, Google GKE, On-Prem and local debugging).

2. Users who want to run SkyPilot tasks on this cluster are issued Kubeconfig
   files containing their credentials (`kube-context <https://kubernetes.io/docs/tasks/access-application-cluster/configure-access-multiple-clusters/#define-clusters-users-and-contexts>`_).
   SkyPilot reads this Kubeconfig file to communicate with the cluster.

Launching your first task
-------------------------
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

   .. note::

     If your cluster administrator has also provided you with a specific service account to use, set it in your ``~/.sky/config.yaml`` file:

     .. code-block:: yaml

         kubernetes:
           remote_identity: your-service-account-name


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

.. _kubernetes-optimizer-table:

4. You can now run any SkyPilot task on your Kubernetes cluster.

   .. code-block:: console

        $ sky launch --cpus 2+ task.yaml
        == Optimizer ==
        Target: minimizing cost
        Estimated cost: $0.0 / hour

        Considered resources (1 node):
        ---------------------------------------------------------------------------------------------------
         INFRA                        INSTANCE          vCPUs   Mem(GB)   GPUS     COST ($)   CHOSEN
        ---------------------------------------------------------------------------------------------------
         Kubernetes (kind-skypilot)   -                 2       2         -        0.00          ✔
         AWS (us-east-1)              m6i.large         2       8         -        0.10
         Azure (eastus)               Standard_D2s_v5   2       8         -        0.10
         GCP (us-central1-a)          n2-standard-2     2       8         -        0.10
         IBM (us-east)                bx2-8x32          8       32        -        0.38
         Lambda (us-east-1)           gpu_1x_a10        30      200       A10:1    0.60
        ----------------------------------------------------------------------------------------------------


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



Viewing cluster status
----------------------

To view the status of all SkyPilot resources in the Kubernetes cluster, run :code:`sky status --k8s`.

Unlike :code:`sky status` which lists only the SkyPilot resources launched by the current user,
:code:`sky status --k8s` lists all SkyPilot resources in the Kubernetes cluster across all users.

.. code-block:: console

    $ sky status --k8s
    Kubernetes cluster state (context: mycluster)
    SkyPilot clusters
    USER     NAME                           LAUNCHED    INFRA      RESOURCES                 STATUS
    alice    infer-svc-1                    23 hrs ago  Kubernetes 1x(gpus=L4:1, ...)        UP
    alice    sky-jobs-controller-80b50983   2 days ago  Kubernetes 1x(cpus=4, mem=4, ...)    UP
    alice    sky-serve-controller-80b50983  23 hrs ago  Kubernetes 1x(cpus=4, mem=4, ...)    UP
    bob      dev                            1 day ago   Kubernetes 1x(gpus=H100:1, ...)      UP
    bob      multinode-dev                  1 day ago   Kubernetes 2x(cpus=2, mem=2, ...)    UP
    bob      sky-jobs-controller-2ea485ea   2 days ago  Kubernetes 1x(cpus=4, mem=4, ...)    UP

    Managed jobs
    In progress tasks: 1 STARTING
    USER     ID  TASK  NAME      REQUESTED   SUBMITTED   TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS
    alice    1   -     eval      1x[CPU:1+]  2 days ago  49s            8s            0            SUCCEEDED
    bob      4   -     pretrain  1x[H100:4]  1 day ago   1h 1m 11s      1h 14s        0            SUCCEEDED
    bob      3   -     bigjob    1x[CPU:16]  1 day ago   1d 21h 11m 4s  -             0            STARTING
    bob      2   -     failjob   1x[CPU:1+]  1 day ago   54s            9s            0            FAILED
    bob      1   -     shortjob  1x[CPU:1+]  2 days ago  1h 1m 19s      1h 16s        0            SUCCEEDED

You can also inspect the real-time GPU usage on the cluster with :code:`sky show-gpus --infra k8s`.

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


Using custom images
-------------------
By default, we maintain and use two SkyPilot container images for use on Kubernetes clusters:

1. ``us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot``: used for CPU-only clusters (`Dockerfile <https://github.com/skypilot-org/skypilot/blob/master/Dockerfile_k8s>`__).
2. ``us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot-gpu``: used for GPU clusters (`Dockerfile <https://github.com/skypilot-org/skypilot/blob/master/Dockerfile_k8s_gpu>`__).

These images are pre-installed with SkyPilot dependencies for fast startup.

To use your own image, add :code:`image_id: docker:<your image tag>` to the :code:`resources` section of your task YAML.

.. code-block:: yaml

    resources:
      image_id: docker:myrepo/myimage:latest
    ...

Your image must satisfy the following requirements:

* Image must be **debian-based** and must have the apt package manager installed.
* The default user in the image must have root privileges or passwordless sudo access.

.. note::

    If your cluster runs on non-x86_64 architecture (e.g., Apple Silicon), your image must be built natively for that architecture. Otherwise, your job may get stuck at :code:`Start streaming logs ...`. See `GitHub issue <https://github.com/skypilot-org/skypilot/issues/3035>`_ for more.

.. _kubernetes-custom-images-private-repos:

Using images from private repositories
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
To use images from private repositories (e.g., Private DockerHub, Amazon ECR, Google Container Registry), create a `secret <https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/#create-a-secret-by-providing-credentials-on-the-command-line>`_ in your Kubernetes cluster and edit your :code:`~/.sky/config.yaml` to specify the secret like so:

.. code-block:: yaml

    kubernetes:
      pod_config:
        spec:
          imagePullSecrets:
            - name: your-secret-here

.. tip::

    If you use Amazon ECR, your secret credentials may expire every 12 hours. Consider using `k8s-ecr-login-renew <https://github.com/nabsul/k8s-ecr-login-renew>`_ to automatically refresh your secrets.


Opening ports
-------------

Opening ports on SkyPilot clusters running on Kubernetes is supported through two modes:

1. `LoadBalancer services <https://kubernetes.io/docs/concepts/services-networking/service/#loadbalancer>`_ (default)
2. `Nginx IngressController <https://kubernetes.github.io/ingress-nginx/>`_

One of these modes must be supported and configured on your cluster. Refer to the :ref:`setting up ports on Kubernetes guide <kubernetes-ports>` on how to do this.

.. tip::

  On Google GKE, Amazon EKS or other cloud-hosted Kubernetes services, the default LoadBalancer services mode is supported out of the box and no additional configuration is needed.

Once your cluster is  configured, launch a task which exposes services on a port by adding :code:`ports` to the :code:`resources` section of your task YAML.

.. code-block:: yaml

    # task.yaml
    resources:
      ports: 8888

    run: |
      python -m http.server 8888

After launching the cluster with :code:`sky launch -c myclus task.yaml`, you can get the URL to access the port using :code:`sky status --endpoints myclus`.

.. code-block:: bash

    # List all ports exposed by the cluster
    $ sky status --endpoints myclus
    8888: 34.173.13.241:8888

    # curl a specific port's endpoint
    $ curl $(sky status --endpoint 8888 myclus)
    ...

.. tip::

    To learn more about opening ports in SkyPilot tasks, see :ref:`Opening Ports <ports>`.

.. _kubernetes-custom-pod-config:

Customizing SkyPilot Pods
-------------------------

You can override the pod configuration used by SkyPilot by setting the :code:`pod_config` key in :code:`~/.sky/config.yaml`.
The value of :code:`pod_config` should be a dictionary that follows the `Kubernetes Pod API <https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.26/#pod-v1-core>`_. This will apply to all pods created by SkyPilot.

For example, to set custom environment variables and use GPUDirect RDMA, you can add the following to your :code:`~/.sky/config.yaml` file:

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      pod_config:
        spec:
          containers:
            - env:                # Custom environment variables to set in pod
              - name: MY_ENV_VAR
                value: MY_ENV_VALUE
              resources:          # Custom resources for GPUDirect RDMA
                requests:
                  rdma/rdma_shared_device_a: 1
                limits:
                  rdma/rdma_shared_device_a: 1


.. tip::

    As an alternative to setting ``pod_config`` globally, you can also set it on a per-task basis directly in your task YAML with the ``config`` :ref:`field <config-client-job-task-yaml>`.

    .. code-block:: yaml

       # task.yaml
       run: |
         python myscript.py

       # Set pod_config for this task
       config:
         kubernetes:
           pod_config:
             ...

.. _kubernetes-using-volumes:

Mounting NFS and other volumes
------------------------------

`Kubernetes volumes <https://kubernetes.io/docs/concepts/storage/volumes/>`_ can be attached to SkyPilot pods using the :ref:`pod_config <kubernetes-custom-pod-config>` field. This is useful for accessing shared storage such as NFS or local high-performance storage like NVMe drives.

Refer to :ref:`kubernetes-setup-volumes` for details and examples.

FAQs
----

* **Can I use multiple Kubernetes clusters with SkyPilot?**

  SkyPilot can work with multiple Kubernetes contexts in your kubeconfig file by setting the ``allowed_contexts`` key in :code:`~/.sky/config.yaml`. See :ref:`multi-kubernetes`.

  If ``allowed_contexts`` is not set, SkyPilot will use the current active context. To use a different context, change your current context using :code:`kubectl config use-context <context-name>`.

* **Are autoscaling Kubernetes clusters supported?**

  To run on autoscaling clusters, set the :code:`provision_timeout` key in :code:`~/.sky/config.yaml` to a large value to give enough time for the cluster autoscaler to provision new nodes.
  This will direct SkyPilot to wait for the cluster to scale up before failing over to the next candidate resource (e.g., next cloud).

  If you are using GPUs in a scale-to-zero setting, you should also set the :code:`autoscaler` key to the autoscaler type of your cluster. More details in :ref:`config-yaml`.

  .. code-block:: yaml

      # ~/.sky/config.yaml
      kubernetes:
        provision_timeout: 900  # Wait 15 minutes for nodes to get provisioned before failover. Set to -1 to wait indefinitely.
        autoscaler: gke  # [gke, karpenter, generic]; required if using GPUs/TPUs in scale-to-zero setting

* **Can SkyPilot provision a Kubernetes cluster for me? Will SkyPilot add more nodes to my Kubernetes clusters?**

  The goal of Kubernetes support is to run SkyPilot tasks on an existing Kubernetes cluster. It does not provision any new Kubernetes clusters or add new nodes to an existing Kubernetes cluster.

* **I have multiple users in my organization who share the same Kubernetes cluster. How do I provide isolation for their SkyPilot workloads?**

  For isolation, you can create separate Kubernetes namespaces and set them in the kubeconfig distributed to users. SkyPilot will use the namespace set in the kubeconfig for running all tasks.

* **How do I view the pods created by SkyPilot on my Kubernetes cluster?**

  You can use your existing observability tools to filter resources with the label :code:`parent=skypilot` (:code:`kubectl get pods -l 'parent=skypilot'`). As an example, follow the instructions :ref:`here <kubernetes-observability>` to deploy the Kubernetes Dashboard on your cluster.

* **Does SkyPilot support TPUs on GKE?**

  SkyPilot supports single-host TPU topologies on GKE (e.g., 1x1, 2x2, 2x4). To use TPUs, add it to the accelerator field in your task YAML:

  .. code-block:: yaml

    resources:
      accelerators: tpu-v5-lite-podslice:1  # or tpu-v5-lite-device, tpu-v5p-slice

* **I am using a custom image. How can I speed up the pod startup time?**

  You can pre-install SkyPilot dependencies in your custom image to speed up the pod startup time. Simply add these lines at the end of your Dockerfile:

  .. code-block:: dockerfile

    FROM <your base image>

    # Install system dependencies
    RUN apt update -y && \
        apt install git gcc rsync sudo patch openssh-server pciutils fuse unzip socat netcat-openbsd curl -y && \
        rm -rf /var/lib/apt/lists/*

    # Install conda and other python dependencies
    RUN curl https://repo.anaconda.com/miniconda/Miniconda3-py310_23.11.0-2-Linux-x86_64.sh -o Miniconda3-Linux-x86_64.sh && \
        bash Miniconda3-Linux-x86_64.sh -b && \
        eval "$(~/miniconda3/bin/conda shell.bash hook)" && conda init && conda config --set auto_activate_base true && conda activate base && \
        grep "# >>> conda initialize >>>" ~/.bashrc || { conda init && source ~/.bashrc; } && \
        rm Miniconda3-Linux-x86_64.sh && \
        export PIP_DISABLE_PIP_VERSION_CHECK=1 && \
        python3 -m venv ~/skypilot-runtime && \
        PYTHON_EXEC=$(echo ~/skypilot-runtime)/bin/python && \
        $PYTHON_EXEC -m pip install 'skypilot-nightly[remote,kubernetes]' 'ray[default]==2.9.3' 'pycryptodome==3.12.0' && \
        $PYTHON_EXEC -m pip uninstall skypilot-nightly -y && \
        curl -LO "https://dl.k8s.io/release/v1.28.11/bin/linux/amd64/kubectl" && \
        sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
        echo 'export PATH="$PATH:$HOME/.local/bin"' >> ~/.bashrc

* **Are multi-node jobs supported on Kubernetes?**

  :ref:`Multi-node jobs <dist-jobs>` are supported on Kubernetes. When a multi-node job is launched, each node in a SkyPilot cluster is provisioned as a separate pod.

  SkyPilot will attempt to place each pod on a different node in the cluster.

  SkyPilot will try to schedule all pods on a given cluster. If SkyPilot cannot schedule all pods on a given cluster (i.e. some or all of the pods cannot be scheduled),
  SkyPilot will fail over to another cluster or cloud.

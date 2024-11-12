.. _kubernetes-getting-started:

Getting Started on Kubernetes
=============================

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



Viewing cluster status
----------------------

To view the status of all SkyPilot resources in the Kubernetes cluster, run :code:`sky status --k8s`.

Unlike :code:`sky status` which lists only the SkyPilot resources launched by the current user,
:code:`sky status --k8s` lists all SkyPilot resources in the Kubernetes cluster across all users.

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

You can also inspect the real-time GPU usage on the cluster with :code:`sky show-gpus --cloud k8s`.

.. code-block:: console

    $ sky show-gpus --cloud k8s
    Kubernetes GPUs
    GPU   REQUESTABLE_QTY_PER_NODE  TOTAL_GPUS  TOTAL_FREE_GPUS
    L4    1, 2, 4                   12          12
    H100  1, 2, 4, 8                16          16

    Kubernetes per node GPU availability
    NODE_NAME                  GPU_NAME  TOTAL_GPUS  FREE_GPUS
    my-cluster-0               L4        4           4
    my-cluster-1               L4        4           4
    my-cluster-2               L4        2           2
    my-cluster-3               L4        2           2
    my-cluster-4               H100      8           8
    my-cluster-5               H100      8           8


.. _kubernetes-custom-images:

Using Custom Images
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

Using Images from Private Repositories
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


Opening Ports
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

FAQs
----

* **Are autoscaling Kubernetes clusters supported?**

  To run on an autoscaling cluster, you may need to adjust the resource provisioning timeout (:code:`Kubernetes.TIMEOUT` in `clouds/kubernetes.py`) to a large value to give enough time for the cluster to autoscale. We are working on a better interface to adjust this timeout - stay tuned!

* **Can SkyPilot provision a Kubernetes cluster for me? Will SkyPilot add more nodes to my Kubernetes clusters?**

  The goal of Kubernetes support is to run SkyPilot tasks on an existing Kubernetes cluster. It does not provision any new Kubernetes clusters or add new nodes to an existing Kubernetes cluster.

* **I have multiple users in my organization who share the same Kubernetes cluster. How do I provide isolation for their SkyPilot workloads?**

  For isolation, you can create separate Kubernetes namespaces and set them in the kubeconfig distributed to users. SkyPilot will use the namespace set in the kubeconfig for running all tasks.

* **How do I view the pods created by SkyPilot on my Kubernetes cluster?**

  You can use your existing observability tools to filter resources with the label :code:`parent=skypilot` (:code:`kubectl get pods -l 'parent=skypilot'`). As an example, follow the instructions :ref:`here <kubernetes-observability>` to deploy the Kubernetes Dashboard on your cluster.

* **How can I specify custom configuration for the pods created by SkyPilot?**

  You can override the pod configuration used by SkyPilot by setting the :code:`pod_config` key in :code:`~/.sky/config.yaml`.
  The value of :code:`pod_config` should be a dictionary that follows the `Kubernetes Pod API <https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.22/#pod-v1-core>`_.

  For example, to set custom environment variables and attach a volume on your pods, you can add the following to your :code:`~/.sky/config.yaml` file:

  .. code-block:: yaml

      kubernetes:
        pod_config:
          spec:
            containers:
              - env:
                - name: MY_ENV_VAR
                  value: MY_ENV_VALUE
                volumeMounts:       # Custom volume mounts for the pod
                  - mountPath: /foo
                    name: example-volume
                resources:          # Custom resource requests and limits
                  requests:
                    rdma/rdma_shared_device_a: 1
                  limits:
                    rdma/rdma_shared_device_a: 1
            volumes:
              - name: example-volume
                hostPath:
                  path: /tmp
                  type: Directory

  For more details refer to :ref:`config-yaml`.

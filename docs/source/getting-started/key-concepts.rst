.. _key-concepts:

========================
Overview
========================

.. TODO: seems ok to just use "cluster" instead of "dev cluster" everywhere?


SkyPilot enables you to combine your cloud infra --- Kubernetes
clusters, cloud accounts/regions for VMs, and existing machines --- into a unified compute pool.

.. .. image:: ../images/skypilot-abstractions-long.png
..     :align: center

.. image:: ../images/skypilot-abstractions-long-2.png
    :align: center

.. .. image:: ../images/skypilot-abstractions.png
..     :width: 400px
..     :align: center

You can then run workloads on this pool in a unified interface, using these core abstractions:

- Clusters
- Jobs
- Serving

.. - :ref:`Dev clusters <concept-dev-clusters>`
.. - :ref:`Jobs <concept-jobs>`
.. - :ref:`Serving <concept-services>`


.. With these, you can use SkyPilot to run all use cases in the entire AI and batch job lifecycle:

These abstractions allow you to run all use cases in the AI and batch job lifecycle:
development, (pre)training, massively parallel tuning/batch inference, and online serving.


.. - :ref:`Jobs on dev clusters <concept-jobs-on-dev-cluster>`
.. - :ref:`Managed jobs <concept-managed-jobs>`

In SkyPilot, every workload benefits from:

.. - **Unified, any-infra**: You use the same way to launch on any cloud infra you own; it is automatically multicloud, multi-region, and multi-cluster.
.. - **Cost and capacity-optimizing**: When launching a workload, SkyPilot will automatically choose
..   the cheapest and most available infra choice in your search space.
.. - **Auto-failover**: If an infra choice is not available, SkyPilot will automatically failover.


.. dropdown:: Unified execution on any cloud, region, and cluster

    Regardless of how many clouds, regions, and clusters you have, you can use a unified interface
    to run workloads on them.

    Your focus on the workload, and SkyPilot alleviates the burden of
    dealing with cloud infra details and differences.

.. dropdown:: Cost and capacity optimization

    When launching a workload, SkyPilot will automatically choose the cheapest and most available infra choice in your search space.

.. dropdown:: Auto-fallback across infra choices

    When launching a workload, you can give SkyPilot a search space of infra
    choices --- as unrestricted or as specific as you like. If an infra choice has no capacity,
    SkyPilot automatically falls back to the next best choice in your infra search space.

.. dropdown:: Future-proof your infra

    Should you add infra choices (e.g., a new cloud, region, or cluster) in the future, your workloads automatically get the ability to leverage them.
    No complex retooling or workflow changes.
    See the underlying :ref:`Sky Computing <sky-computing>` vision.


.. At its core, SkyPilot provides a "kernel", the ``sky launch`` CLI/API, that forms the basis of all three
.. abstractions.

.. ``sky launch`` is used to launch dev clusters that is (1) natively multi-cloud/cluster/region, with auto-failover; (2) optimizing for cost and capacity.
.. Managed jobs and services are then implemented on top of ``sky launch``, and therefore automatically inherit all of the benefits above.


.. _concept-dev-clusters:

Clusters
------------


.. Dev clusters are a set of nodes (VMs; or pods in Kubernetes) that you launch with ``sky launch``.

.. You can use ``sky launch`` to launch a dev cluster, which is a set of *nodes*
.. (VMs, or pods in Kubernetes). A cluster is the core compute resource unit in
.. SkyPilot.

.. A cluster is a set of nodes --- VMs, or pods in Kubernetes --- which are interconnected in one location (the same zone/k8s cluster).

A *cluster* is SkyPilot's core resource unit: a set of VMs or Kubernetes pods in the same location.

.. A *cluster* is a set of VMs or Kubernetes pods in the same location.
.. It is the core resource unit in SkyPilot.

You can use ``sky launch`` to launch a cluster:

.. tab-set::

    .. tab-item:: CLI
        :sync: cli

        .. code-block:: console

            $ sky launch
            $ sky launch --gpus L4:8
            $ sky launch --num-nodes 10 --cpus 2+ --memory 2+ --down
            $ sky launch cluster.yaml
            $ sky launch --help  # See all flags.

    .. tab-item:: Python
        :sync: python

        .. code-block:: python

            import sky
            task = sky.Task().set_resources(sky.Resources(accelerators='L4:8'))
            sky.launch(task, cluster_name='my-cluster')

You can do the following with a cluster:

- SSH into any node
- Connect VSCode/IDE to it
- Queue many jobs on it
- Have it automatically shut down or stop after jobs finish
- Easily launch and use many lightweight, ephemeral clusters

.. - Treat it as your dev machine on the cloud
.. - ...and more

.. A dev cluster's spec (e.g., resource spec; setup commands) is declaratively written in a YAML file.
You can optionally bring your custom Docker or VM image when launching, or use SkyPilot's sane defaults, which configure the correct CUDA versions for different GPUs.

See :ref:`quickstart` and :ref:`dev-cluster` to get started.

.. tip::

    Think of clusters as *virtual* in nature. They can be launched on *physical*
    clusters you bring to SkyPilot, such as :ref:`Kubernetes clusters
    <concept-kubernetes-clusters>` or :ref:`existing machines
    <concept-existing-machines>`.

    *Terminology*: "Clusters" and "dev clusters" are used interchangeably.


.. _concept-jobs:

Jobs
------------

A *job* is a program you want to run.

.. A job can contain one or more tasks; that said, most jobs have only one task, and we will refer to "job" and "task" interchangeably.

.. tip::

    *Terminology*: A job can contain one or :ref:`more <pipeline>` tasks. In most cases, a job has just one task; we'll refer to them interchangeably.

    .. *Terminology*: While :ref:`certain jobs <pipeline>` can have multiple tasks, most jobs have only one task, where we will refer to "job" and "task" interchangeably.



.. _concept-jobs-on-dev-cluster:

Jobs on clusters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can use ``sky exec`` to queue and run jobs on an existing cluster.
This is ideal for interactive development.

See :ref:`job-queue` to get started.

.. tab-set::

    .. tab-item:: CLI
        :sync: cli

        .. code-block:: bash

            sky exec my-cluster --gpus L4:1 --workdir=. -- python train.py
            sky exec my-cluster train.yaml  # Specify everything in a YAML.

            # Fractional GPUs are also supported.
            sky exec my-cluster --gpus L4:0.5 -- python eval.py

            # A job with no GPU requirement.
            sky exec my-cluster -- echo "Hello, SkyPilot!"

    .. tab-item:: Python
        :sync: python

        .. code-block:: python

            # Assume you have 'my-cluster' already launched.

            # Queue a job requesting 1 GPU.
            train = sky.Task(run='python train.py').set_resources(
                sky.Resources(accelerators='L4:1'))
            sky.exec(train, cluster_name='my-cluster', detach_run=True)

            # Queue a job requesting 0.5 GPU.
            eval = sky.Task(run='python eval.py').set_resources(
                sky.Resources(accelerators='L4:0.5'))
            sky.exec(eval, cluster_name='my-cluster', detach_run=True)


.. _concept-managed-jobs:

Managed jobs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


*Managed jobs* automatically provision a temporary cluster for each job and handle
auto-recovery. A lightweight jobs controller is used to offer hands-off monitoring, recovery, and cleanup.
You can use ``sky jobs launch`` to launch managed jobs.

.. A *managed job* runs on its own job-scoped cluster, and it
.. comes with auto-recovery offered by a lightweight jobs controller.

Suggested pattern: Use clusters to interactively develop and debug your code first, and then
use managed jobs to run them at scale.

See :ref:`managed-jobs` and :ref:`many-jobs` to get started.

.. .. tip::

..     .. **Terminology**:

..     A managed job can contain multiple tasks (see :ref:`pipelines <pipeline>`). When a job has only one task, as is the common case, "job" and "task" are used interchangeably.


.. _concept-services:

Services
--------

A *service* is for AI model serving.
A service can have one or more replicas, potentially spanning across locations (regions, clouds, clusters), pricing models (on-demand, spot, etc.), or even GPU types.

.. Each service can have multiple
.. replicas---potentially spanning different locations (clouds, regions, clusters),
.. pricing models (on-demand, spot), or GPU types. A lightweight service controller offers load balancing, monitoring, and replica recovery.

.. A *service* is used for serving AI models.

.. Think of each replica as a cluster, launched by ``sky launch``.

See :ref:`sky-serve` to get started.

.. TODO: seeing is believing. Add snippet (cli + api).

Bringing your infra
-------------------------------------------------------------------

.. SkyPilot is designed to easily connect to your existing infra.
.. By default, existing auth is reused.
SkyPilot easily connects to your existing infra---cloud accounts, Kubernetes clusters, or on-prem machines---using each infra's standard authentication (cloud credentials, kubeconfig, SSH).

Cloud VMs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot can launch VMs on the clouds and regions you have access to.
Run ``sky check`` to check access.

SkyPilot supports most major cloud providers. See :ref:`cloud-account-setup` for details.

.. raw:: html

   <p align="center">
   <picture>
      <img class="only-light" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
      <img class="only-dark" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png" width=85%>
   </picture>
   </p>

By default, SkyPilot reuses your existing cloud authentication methods.  Optionally, you can also :ref:`set up <cloud-permissions>` specific roles, permissions, or service accounts for SkyPilot to use.

.. .. tip::

..     Cloud VMs are the most flexible option because they provide many regions and hardware
..     options. This can maximally improve GPU availability and cost savings.

.. _concept-kubernetes-clusters:

Kubernetes clusters
~~~~~~~~~~~~~~~~~~~~~

You can bring existing Kubernetes clusters, including managed clusters (e.g.,
EKS, GKE, AKS) or on-prem ones, into SkyPilot.  Auto-fallback and failover
between multiple Kubernetes clusters is also supported.

See :ref:`kubernetes-overview`.

.. figure:: ../images/k8s-skypilot-architecture-dark.png
   :width: 45%
   :align: center
   :alt: SkyPilot on Kubernetes
   :class: no-scaled-link, only-dark

   SkyPilot layers on top of your Kubernetes cluster(s).

.. figure:: ../images/k8s-skypilot-architecture-light.png
   :width: 45%
   :align: center
   :alt: SkyPilot on Kubernetes
   :class: no-scaled-link, only-light

   SkyPilot layers on top of your Kubernetes cluster(s).

.. _concept-existing-machines:

Existing machines
~~~~~~~~~~~~~~~~~~~~~

If you have existing machines, i.e., a list of IP addresses you can SSH into, you can bring them into SkyPilot.

See :ref:`Using Existing Machines <existing-machines>`.

.. figure:: ../images/sky-existing-infra-workflow-light.png
   :width: 85%
   :align: center
   :alt: Deploying SkyPilot on existing machines
   :class: no-scaled-link, only-light

.. figure:: ../images/sky-existing-infra-workflow-dark.png
   :width: 85%
   :align: center
   :alt: Deploying SkyPilot on existing machines
   :class: no-scaled-link, only-dark

.. ``sky launch``: Any-infra provisioner and orchestrator

``sky launch``: Cost and capacity-optimizing provisioner
-------------------------------------------------------------------

.. TODO: Kind of weird to call an CLI a provisioner/orchestrator. Also, this is describing the hammer.

How does SkyPilot offer (1) unified execution across infra, and (2) cost and capacity optimization?

.. , and all higher-level abstractions/libraries (managed jobs; serving) util
.. upon it.

.. is used to launch dev clusters. It


In SkyPilot, ``sky launch``  is the core "kernel"
that delivers these benefits.  It is used to launch all underlying compute resources.
Every ``sky launch`` performs the following:

- Natively optimizes for cost and capacity in the given search space
- Provisions compute resources with auto-fallback
- Sets up the environment (images, dependencies, file mounts, etc.) in an infrastructure-as-code manner

For example, if you want to launch 8 A100 GPUs, SkyPilot will try all infra
options in the given search space  in the "cheapest and most available" order,
offering auto-fallback:

.. figure:: https://blog.skypilot.co/ai-on-kubernetes/images/failover.png
   :width: 85%
   :align: center
   :alt: SkyPilot auto-failover
   :class: no-scaled-link

As such, SkyPilot users no longer need to worry about specific infra details, manual retry, or manual setup.
Workloads obtain higher GPU capacity and cost savings.

Every launch can take a search space that is as flexible (e.g., use any of the accessible infra; any of the supported GPUs) or as specific (e.g., must use a specific zone or cloud) as you need.
Optimization automatically occurs within the search space.

See :ref:`auto-failover` for more details.


.. - Schedules, executes, and monitors the workload on the compute resources.

.. ``sky launch`` offers several unique benefits:

.. - Automatically multicloud, multi-region, and multi-cluster: this is a ``any-infra`` kernel.
.. - Cost and capacity-optimizing: ``sky launch``
.. - Auto-failover:
.. - As flexible or as specific as you need: you can either leave the resource
..   specification as flexible as possible, or get as specific as your workload
..   requires.

.. TODO: As flexible or as specific as you need:

.. _kubernetes-overview:

Using Kubernetes
================

SkyPilot tasks can be run on your private on-prem or cloud Kubernetes clusters.
The Kubernetes cluster gets added to the list of "clouds" in SkyPilot and SkyPilot
tasks can be submitted to your Kubernetes cluster just like any other cloud provider.

Why use SkyPilot on Kubernetes?
-------------------------------

.. tab-set::

    .. tab-item:: For AI Developers
        :sync: why-ai-devs-tab

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  ✅ Ease of use
                :text-align: center

                ..
                    TODO(romilb): We should have a comparison of a popular Kubernetes manifest vs a SkyPilot YAML in terms of LoC in a mini blog and link it here.

                No complex kubernetes manifests - write a simple SkyPilot YAML and run with one command ``sky launch``.

            .. grid-item-card::  📋 Interactive development on Kubernetes
                :text-align: center

                :ref:`SSH access to pods <dev-ssh>`, :ref:`VSCode integration <dev-vscode>`, :ref:`job management <managed-jobs>`, :ref:`autodown idle pods <auto-stop>` and more.

            .. grid-item-card::  ☁️ Burst to the cloud
                :text-align: center

                Kubernetes cluster is full? SkyPilot :ref:`seamlessly gets resources on the cloud <kubernetes-optimizer-table>` to get your job running sooner.

            .. grid-item-card::  🖼 Run popular models on Kubernetes
                :text-align: center

                Train and serve `Llama-3 <https://docs.skypilot.co/en/latest/gallery/llms/llama-3.html>`_, `Mixtral <https://docs.skypilot.co/en/latest/gallery/llms/mixtral.html>`_, and more on your Kubernetes with ready-to-use recipes from the :ref:`Examples <examples>`.


    .. tab-item:: For Infrastructure Admins
        :sync: why-admins-tab

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  ☁️ Unified platform for all Infrastructure
                :text-align: center

                Scale beyond your Kubernetes cluster to capacity on :ref:`across clouds and regions <auto-failover>` without manual intervention.

            .. grid-item-card::  🚯️ Minimize resource wastage
                :text-align: center

                SkyPilot can run with your custom pod scheduler and automatically terminate idle pods to free up resources for other users.

            .. grid-item-card::  👀 Observability
                :text-align: center

                Works with your existing observability and monitoring tools, such as the :ref:`Kubernetes Dashboard <kubernetes-observability>`.

            .. grid-item-card::  🍽️ Self-serve infra for your teams
                :text-align: center

                Reduce operational overhead by letting your teams provision their own resources, while you retain control over the Kubernetes cluster.


Table of contents
-----------------

.. grid:: 1 1 3 3
    :gutter: 3

    .. grid-item-card::  👋 Get Started
        :link: kubernetes-getting-started
        :link-type: ref
        :text-align: center

        Already have a kubeconfig? Launch your first SkyPilot task on Kubernetes - it's as simple as ``sky launch``.

    .. grid-item-card::  ⚙️ Cluster Configuration
        :link: kubernetes-setup
        :link-type: ref
        :text-align: center

        Are you a cluster admin? Find cluster deployment guides and setup instructions here.

    .. grid-item-card::  🔍️ Troubleshooting
        :link: kubernetes-troubleshooting
        :link-type: ref
        :text-align: center

        Running into problems with SkyPilot on your Kubernetes cluster? Find common issues and solutions here.


.. toctree::
   :hidden:

   Getting Started <kubernetes-getting-started>
   kubernetes-setup
   kubernetes-priorities
   multi-kubernetes
   SkyPilot vs. Vanilla Kubernetes <skypilot-and-vanilla-k8s>
   Examples <examples/index>
   kubernetes-troubleshooting

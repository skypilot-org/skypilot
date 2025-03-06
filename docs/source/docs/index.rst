Welcome to SkyPilot!
====================

.. image:: /_static/SkyPilot_wide_dark.svg
  :width: 50%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link, only-dark
.. image:: /_static/SkyPilot_wide_light.svg
  :width: 50%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link, only-light


.. raw:: html

   <p></p>
   <p style="text-align:center">
   <strong>Run AI on Any Infra</strong> — Unified, Faster, Cheaper
   </p>
   <p style="text-align:center">
   <a class="github-button" href="https://github.com/skypilot-org/skypilot" data-show-count="true" data-size="large" aria-label="Star skypilot-org/skypilot on GitHub">Star</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/subscription" data-icon="octicon-eye" data-size="large" aria-label="Watch skypilot-org/skypilot on GitHub">Watch</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/fork" data-icon="octicon-repo-forked" data-size="large" aria-label="Fork skypilot-org/skypilot on GitHub">Fork</a>
   <a class="reference external image-reference" style="vertical-align:9.5px" href="http://slack.skypilot.co"><img src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack" style="height:27px"></a>
   <script async defer src="https://buttons.github.io/buttons.js"></script>
   </p>


SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.

SkyPilot **abstracts away infra burdens**:

- Launch :ref:`clusters <dev-cluster>`, :ref:`jobs <managed-jobs>`, and :ref:`serving <sky-serve>` on any infra
- Easy job management: queue, run, and auto-recover many jobs

SkyPilot **supports multiple clusters, clouds, and hardware** (`the Sky <https://arxiv.org/abs/2205.07147>`_):

- Bring your reserved GPUs, Kubernetes clusters, or 12+ clouds
- :ref:`Flexible provisioning <auto-failover>` of GPUs, TPUs, CPUs, with auto-retry

SkyPilot **cuts your cloud costs & maximizes GPU availability**:

* :ref:`Autostop <auto-stop>`: automatic cleanup of idle resources
* :ref:`Managed Spot <managed-jobs>`: 3-6x cost savings using spot instances, with preemption auto-recovery
* :ref:`Optimizer <auto-failover>`: 2x cost savings by auto-picking the cheapest & most available infra

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.


:ref:`Current supported infra <installation>` (Kubernetes; AWS, GCP, Azure, OCI, Lambda Cloud, Fluidstack, RunPod, Cudo, Paperspace, Digital Ocean, Cloudflare, Samsung, IBM, Vast.ai, VMware vSphere):

.. raw:: html

   <p align="center">
   <picture>
      <img class="only-light" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
      <img class="only-dark" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png" width=85%>
   </picture>
   </p>

Ready to get started?
----------------------

:ref:`Install SkyPilot <installation>` in 1 minute. Then, launch your first cluster in 2 minutes in :ref:`Quickstart <quickstart>`.

SkyPilot is BYOC: Everything is launched within your cloud accounts, VPCs, and clusters.

Contact the SkyPilot team
---------------------------------

You can chat with the SkyPilot team and community on the `SkyPilot Slack <http://slack.skypilot.co>`_.

Learn more
--------------------------

To learn more, see :ref:`SkyPilot Overview <overview>` and `SkyPilot blog <https://blog.skypilot.co/>`_.

Case Studies and integrations: `Community Spotlights <https://blog.skypilot.co/community/>`_

Follow updates:

* `Slack <http://slack.skypilot.co>`_
* `X / Twitter <https://twitter.com/skypilot_org>`_
* `LinkedIn <https://www.linkedin.com/company/skypilot-oss/>`_
* `SkyPilot Blog <https://blog.skypilot.co/>`_ (`Introductory blog post <https://blog.skypilot.co/introducing-skypilot/>`_)

Read the research:

* `SkyPilot paper <https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf>`_ and `talk <https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_ (NSDI 2023)
* `Sky Computing whitepaper <https://arxiv.org/abs/2205.07147>`_
* `Sky Computing vision paper <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ (HotOS 2021)
* `SkyServe: AI serving across regions and clouds <https://arxiv.org/pdf/2411.01438>`_ (EuroSys 2025)
* `Managed jobs spot instance policy <https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao>`_ (NSDI 2024)

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Getting Started

   ../overview
   ../getting-started/installation
   ../getting-started/quickstart
   ../examples/index
   ../sky-computing

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Clusters

   Start a Cluster <../examples/interactive-development>
   ../examples/auto-failover
   ../reference/auto-stop

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Jobs

   Cluster Jobs <../reference/job-queue>
   ../examples/managed-jobs
   Multi-Node Jobs <../running-jobs/distributed-jobs>
   Many Parallel Jobs <../running-jobs/many-jobs>


.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Model Serving

   Getting Started <../serving/sky-serve>
   ../serving/user-guides

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Infra Choices

   ../reference/kubernetes/index
   Using Existing Machines <../reservations/existing-machines>
   ../reservations/reservations
   Using Cloud VMs <../compute/cloud-vm>
   ../compute/gpus



.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Data

   ../reference/storage
   ../examples/syncing-code-artifacts

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: User Guides

   ../reference/async
   Secrets and Environment Variables <../running-jobs/environment-variables>
   Docker Containers <../examples/docker-containers>
   ../examples/ports
   ../reference/logging
   ../reference/faq

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Administrator Guides

   ../reference/api-server/api-server
   ../cloud-setup/cloud-permissions/index
   ../cloud-setup/cloud-auth
   ../cloud-setup/quota
   Admin Policies <../cloud-setup/policy>

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: References

   Task YAML <../reference/yaml-spec>
   CLI <../reference/cli>
   ../reference/api
   ../reference/config
   ../developers/index


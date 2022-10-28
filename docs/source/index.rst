Welcome to SkyPilot!
=========================

.. figure:: ./images/skypilot-wide-light-1k.png
  :width: 60%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link

.. raw:: html

   <p style="text-align:center">
   <a class="reference external image-reference" style="vertical-align:9.5px" href="https://join.slack.com/t/skypilot-org/shared_invite/zt-1i4pa7lyc-g6Lo4_rqqCFWOSXdvwTs3Q"><img src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack" style="height:27px"></a>
   <script async defer src="https://buttons.github.io/buttons.js"></script>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot" data-show-count="true" data-size="large" aria-label="Star skypilot-org/skypilot on GitHub">Star</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/subscription" data-icon="octicon-eye" data-size="large" aria-label="Watch skypilot-org/skypilot on GitHub">Watch</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/fork" data-icon="octicon-repo-forked" data-size="large" aria-label="Fork skypilot-org/skypilot on GitHub">Fork</a>
   </p>

   <p style="text-align:center">
   <strong>Run jobs on any cloud, easily and cost effectively</strong>
   </p>

SkyPilot is a framework for easily and cost effectively running ML workloads on any cloud.

SkyPilot abstracts away cloud infra burden:

- Launch jobs & clusters on any cloud (AWS, Azure, GCP)
- Find scarce resources across zones/regions/clouds
- Queue jobs & use cloud object stores

SkyPilot cuts your cloud costs:

* :ref:`Managed Spot <Managed Spot Jobs>`: **3x cost savings** using spot VMs, with auto-recovery from preemptions
* :ref:`Autostop <Auto-stopping>`: hands-free cleanup of idle clusters
* :ref:`Benchmark <SkyPilot Benchmark>`: find best VM types for your jobs
* Optimizer: **2x cost savings** by auto-picking best prices across zones/regions/clouds

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

Documentation
--------------------------

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   getting-started/installation
   getting-started/quickstart
   getting-started/tutorial
   examples/gpu-jupyter


.. toctree::
   :maxdepth: 1
   :caption: Running Jobs

   reference/job-queue
   reference/auto-stop
   examples/spot-jobs
   reference/tpu
   running-jobs/index

.. toctree::
   :maxdepth: 1
   :caption: Using Data

   examples/syncing-code-artifacts
   reference/storage


.. toctree::
   :maxdepth: 1
   :caption: User Guides

   reference/local/index
   reference/benchmark/index
   examples/iterative-dev-project
   examples/auto-failover
   reference/interactive-nodes
   reference/quota
   reference/logging
   reference/faq

.. toctree::
   :maxdepth: 1
   :caption: References

   reference/yaml-spec
   reference/cli
   reference/api

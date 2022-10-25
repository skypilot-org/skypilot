SkyPilot Documentation
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

SkyPilot is a framework for easily running machine learning workloads on any cloud.

Use the clouds **easily** and **cost effectively**, without needing cloud infra expertise.

*Ease of use*

- **Run existing projects on the cloud** with zero code changes
- Use a **unified interface** to run on any cloud, without vendor lock-in (currently AWS, Azure, GCP)
- **Queue jobs** on one or multiple clusters
- **Automatic failover** to find scarce resources (GPUs) across regions and clouds
- **Use datasets on the cloud** like you would on a local file system

*Cost saving*

- Run jobs on **spot instances** with **automatic recovery** from preemptions
- Hands-free cluster management: **automatically stopping idle clusters**
- One-click use of **TPUs**, for high-performance, cost-effective training
- Automatically benchmark and find the cheapest hardware for your job

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   getting-started/installation
   getting-started/quickstart
   getting-started/tutorial


.. toctree::
   :maxdepth: 1
   :caption: Use Cases

   examples/iterative-dev-project
   examples/syncing-code-artifacts
   examples/auto-failover
   examples/grid-search
   examples/distributed-jobs
   examples/gpu-jupyter


.. toctree::
   :maxdepth: 1
   :caption: Features

   reference/job-queue
   reference/auto-stop
   examples/spot-jobs
   reference/benchmark/index
   reference/tpu

.. toctree::
   :maxdepth: 1
   :caption: User Guides

   reference/yaml-spec
   reference/interactive-nodes
   reference/storage
   reference/local/index
   reference/quota
   reference/logging
   reference/faq

.. toctree::
   :maxdepth: 1
   :caption: API References

   reference/cli
   reference/api


.. .. toctree::
..   :maxdepth: 1
..   :caption: Advanced SkyPilot Tutorials

..   .. advanced/distributed
..   advanced/python-control


.. .. toctree::
..    :maxdepth: 1
..    :caption: Developer Documentation

..   developers/contributing
..   developers/testing
..   developers/design


.. .. toctree::
..   :maxdepth: 3
..   :caption: API documentation

..   sky

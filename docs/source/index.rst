SkyPilot Documentation
=========================

.. figure:: ./images/skypilot-wide-light-1k.png
  :width: 60%
  :align: center
  :alt: SkyPilot

SkyPilot is a framework for easily running machine learning workloads on any cloud.

Use the clouds **easily** and **cost effectively**, without needing cloud infra expertise.

*Ease of use & productivity*

- **Run existing projects on the cloud** with zero code changes
- **Easily manage jobs** across multiple clusters
- **Automatic fail-over** to find scarce resources (GPUs) across regions and clouds
- **Store datasets on the cloud** and access them like you would on a local file system
- **No cloud lock-in** – seamlessly run your code across different cloud providers (AWS, Azure or GCP)

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
   :caption: SkyPilot CLI

   reference/cli


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

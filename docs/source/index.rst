SkyPilot Documentation
======================

.. figure:: ./images/skypilot-wide-light-1k.png
  :width: 60%
  :align: center
  :alt: SkyPilot

SkyPilot is a framework for easily running machine learning workloads on any cloud through a unified interface.

No knowledge of cloud offerings is required or expected – you simply define the workload and its resource requirements, and SkyPilot will automatically execute it on AWS, Google Cloud Platform or Microsoft Azure.

Key features:

- **Run existing projects on the cloud** with zero code changes
- **No cloud lock-in** – seamlessly run your code across different cloud providers (AWS, Azure or GCP)
- **Minimize costs** by leveraging spot instances and automatically stopping idle clusters
- **Automatic recovery from spot instance failures**
- **Automatic fail-over** to find resources across regions and clouds
- **Store datasets on the cloud** and access them like you would on a local file system
- **Easily manage job queues** across multiple clusters


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

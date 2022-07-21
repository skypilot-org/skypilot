Sky reference documentation
===================================

Sky is a framework for easily running machine learning projects on any cloud through a unified interface.

Key features:

- **Run existing projects on the cloud with zero code changes**
- **Easily provision VMs** across multiple cloud platforms (AWS, Azure or GCP)
- **Easily manage multiple clusters** to handle different projects
- **Quick access** to cloud instances for development
- **Store datasets on the cloud** and access them like you would on a local file system
- **No cloud lock-in** -- seamlessly run your code across cloud providers


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

.. Additional Examples <https://github.com/concretevitamin/sky-experiments/tree/master/prototype/examples>


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
   :caption: Sky CLI

   reference/cli


.. .. toctree::
..   :maxdepth: 1
..   :caption: Advanced Sky Tutorials

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

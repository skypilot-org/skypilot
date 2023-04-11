Welcome to SkyPilot!
=========================

.. figure:: ./images/skypilot-wide-light-1k.png
  :width: 60%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link

.. raw:: html

   <p style="text-align:center">
   <a class="reference external image-reference" style="vertical-align:9.5px" href="http://slack.skypilot.co"><img src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack" style="height:27px"></a>
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

- Launch jobs & clusters on any cloud (AWS, Azure, GCP, Lambda Cloud)
- Find scarce resources across zones/regions/clouds
- Queue jobs & use cloud object stores

SkyPilot cuts your cloud costs:

* :ref:`Managed Spot <Managed Spot Jobs>`: **3x cost savings** using spot VMs, with auto-recovery from preemptions
* :ref:`Autostop <Auto-stopping>`: hands-free cleanup of idle clusters
* :ref:`Benchmark <Benchmark: Find the Best Hardware for Your Jobs>`: find best VM types for your jobs
* Optimizer: **2x cost savings** by auto-picking best prices across zones/regions/clouds

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

**More information**

* `Project blog <https://blog.skypilot.co/>`_
* `Introductory blog post <https://blog.skypilot.co/introducing-skypilot/>`_
* `SkyPilot Tutorials <https://github.com/skypilot-org/skypilot-tutorial>`_
* Framework examples: `PyTorch DDP <https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_torch.yaml>`_,  `Distributed <https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_tf_app.py>`_ `TensorFlow <https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_app_storage.yaml>`_, `JAX/Flax on TPU <https://github.com/skypilot-org/skypilot/blob/master/examples/tpu/tpuvm_mnist.yaml>`_, `Stable Diffusion <https://github.com/skypilot-org/skypilot/tree/master/examples/stable_diffusion>`_, `Detectron2 <https://github.com/skypilot-org/skypilot/blob/master/examples/detectron2_docker.yaml>`_, `programmatic grid search <https://github.com/skypilot-org/skypilot/blob/master/examples/huggingface_glue_imdb_grid_search_app.py>`_, `Docker <https://github.com/skypilot-org/skypilot/blob/master/examples/docker/echo_app.yaml>`_, and `many more <https://github.com/skypilot-org/skypilot/tree/master/examples>`_.

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
   reference/tpu
   examples/auto-failover
   running-jobs/index

.. toctree::
   :maxdepth: 1
   :caption: Cutting Cloud Costs

   examples/spot-jobs
   reference/auto-stop
   reference/benchmark/index

.. toctree::
   :maxdepth: 1
   :caption: Using Data

   examples/syncing-code-artifacts
   reference/storage

.. toctree::
   :maxdepth: 1
   :caption: User Guides

   reference/local/index
   examples/iterative-dev-project
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

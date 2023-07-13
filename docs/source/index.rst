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
   <strong>Run LLMs and AI on Any Cloud</strong>
   </p>

SkyPilot is a framework for running LLMs, AI, and batch jobs on any cloud, offering maximum cost savings, highest GPU availability, and managed execution.

SkyPilot **abstracts away cloud infra burdens**:

- Launch jobs & clusters on any cloud
- Easy scale-out: queue and run many jobs, automatically managed
- Easy access to object stores (S3, GCS, R2)

SkyPilot **maximizes GPU availability for your jobs**:

* Provision in all zones/regions/clouds you have access to (`the Sky <https://arxiv.org/abs/2205.07147>`_), with automatic failover

SkyPilot **cuts your cloud costs**:

* `Managed Spot <https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html>`_: 3-6x cost savings using spot VMs, with auto-recovery from preemptions
* Optimizer: 2x cost savings by auto-picking the cheapest VM/zone/region/cloud
* `Autostop <https://skypilot.readthedocs.io/en/latest/reference/auto-stop.html>`_: hands-free cleanup of idle clusters

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

Current supported providers (AWS, Azure, GCP, Lambda Cloud, IBM, Samsung, OCI, Cloudflare):

.. raw:: html

   <p align="center">
   <picture>
      <a href="https://skypilot.readthedocs.io/en/latest/getting-started/installation.html">
      <img alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=80%></a>
   </picture>
   </p>


**More information**

* `SkyPilot blog <https://blog.skypilot.co/>`_
* `Introductory blog post <https://blog.skypilot.co/introducing-skypilot/>`_
* `NSDI 2023 paper & talk <https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_
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

   examples/docker-containers
   examples/iterative-dev-project
   reference/interactive-nodes
   reference/faq
   reference/logging
   reference/local/index

.. toctree::
   :maxdepth: 1
   :caption: Cloud Admin and Usage

   cloud-setup/cloud-permissions/index
   cloud-setup/cloud-auth
   cloud-setup/quota


.. toctree::
   :maxdepth: 1
   :caption: References

   reference/yaml-spec
   reference/cli
   reference/api

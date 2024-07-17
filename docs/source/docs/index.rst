Welcome to SkyPilot!
====================

.. image:: /_static/SkyPilot_wide_dark.svg
  :width: 65%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link, only-dark
.. image:: /_static/SkyPilot_wide_light.svg
  :width: 60%
  :align: center
  :alt: SkyPilot
  :class: no-scaled-link, only-light

.. raw:: html

   <p style="text-align:center">
   <a class="github-button" href="https://github.com/skypilot-org/skypilot" data-show-count="true" data-size="large" aria-label="Star skypilot-org/skypilot on GitHub">Star</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/subscription" data-icon="octicon-eye" data-size="large" aria-label="Watch skypilot-org/skypilot on GitHub">Watch</a>
   <a class="github-button" href="https://github.com/skypilot-org/skypilot/fork" data-icon="octicon-repo-forked" data-size="large" aria-label="Fork skypilot-org/skypilot on GitHub">Fork</a>
   <br>
   <a class="reference external image-reference" style="vertical-align:9.5px" href="http://slack.skypilot.co"><img src="https://img.shields.io/badge/SkyPilot-Join%20Slack-blue?logo=slack" style="height:27px"></a>
   <script async defer src="https://buttons.github.io/buttons.js"></script>
   </p>

   <p style="text-align:center">
   <strong>Run AI on Any Infra</strong>
   </p>

SkyPilot is a framework for running LLMs, AI, and batch jobs on any infrastructure (12+ clouds and Kubernetes).

**Developer friendly**:

- One command to launch jobs & clusters on any infra
- Easy scale-out: queue and run many jobs, automatically managed
- SSH and VSCode integration out of the box
- Easy access to object stores (S3, GCS, Azure, R2)

**Maximum GPU availability**:

* Provision in all zones/regions/cloud/kubernetes clusters you have access to (`the Sky <https://arxiv.org/abs/2205.07147>`_), with automatic failover
* If your Kubernetes cluster is full, SkyPilot bursts to cloud

**Cost efficient**:

* `Managed Spot <https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html>`_: 3-6x cost savings using spot VMs, with auto-recovery from preemptions
* Optimizer: 2x cost savings by auto-picking the cheapest VM/zone/region/cloud
* `Autostop <https://skypilot.readthedocs.io/en/latest/reference/auto-stop.html>`_: hands-free cleanup of idle clusters

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

Current supported providers (AWS, GCP, Azure, OCI, Lambda Cloud, RunPod, Fluidstack, Cudo, IBM, Samsung, Cloudflare, VMware vSphere, any Kubernetes cluster):

.. raw:: html

   <p align="center">
   <picture>
      <img class="only-light" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
      <img class="only-dark" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png" width=85%>
   </picture>
   </p>

Why use SkyPilot?
-----------------


.. tab-set::

    .. tab-item:: Single Cloud Users
        :sync: single-cloud-tab

        .. raw:: html

            <p style="text-align:center">Have access to only one cloud? SkyPilot helps you make the most of it. </p>

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  ✅ Ease of use
                :text-align: center

                One command -- ``sky launch`` -- to launch repeatable runs. :ref:`SSH <dev-ssh>`, :ref:`VSCode integration <dev-vscode>`, :ref:`job queueing <managed-jobs>` supported out of the box.

            .. grid-item-card::  💰 Cost savings
                :text-align: center

                :ref:`Spot instances <spot-jobs>` save upto 6x, SkyPilot optimizer picks the cheapest resources for you, no more idle resource wastage with :ref:`autostop <auto-stop>`.

            .. grid-item-card::  🚀 Maximum availability
                :text-align: center

                SkyPilot's failover finds GPUs across regions, ensuring your job runs as soon as possible.

            .. grid-item-card:: 🔮 Future-proof
                :text-align: center

                SkyPilot is cloud-agnostic and moving to a new cloud is as simple as adding a flag. No rewrites required when you switch clouds.

    .. tab-item:: Kubernetes only Users
        :sync: multi-infra-tab

        Running on Kubernetes? SkyPilot makes your workflows faster and simpler.

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  ✅ Ease of use
                :text-align: center

                No complex kubernetes manifests - write a simple SkyPilot YAML and run with one command ``sky launch``.

            .. grid-item-card::  📋 Interactive development on Kubernetes
                :text-align: center

                :ref:`SSH access to pods <dev-ssh>`, :ref:`VSCode integration <dev-vscode>`, :ref:`job management <managed-jobs>`, :ref:`autodown idle pods <auto-stop>` and more.

            .. grid-item-card::  ☁️ Burst to the cloud
                :text-align: center

                Kubernetes cluster is full? SkyPilot :ref:`seamlessly gets resources on the cloud <kubernetes-optimizer-table>` to get your job running sooner.

            .. grid-item-card::  🖼 Run popular models on Kubernetes
                :text-align: center

                Train and serve `Llama-3 <https://skypilot.readthedocs.io/en/latest/gallery/llms/llama-3.html>`_, `Mixtral <https://skypilot.readthedocs.io/en/latest/gallery/llms/mixtral.html>`_, and more on your Kubernetes with ready-to-use recipes from the :ref:`AI gallery <ai-gallery>`.

    .. tab-item:: Multi Infra Users
        :sync: multi-infra-tab

        Have access to multiple clouds and/or Kubernetes clusters? SkyPilot was designed to get the most out of your resources.

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  ✅ Ease of use
                :text-align: center

                One command to launch repeatable runs on any infra. Same features, :ref:`SSH access <dev-ssh>`, :ref:`VSCode integration <dev-vscode>`, :ref:`job queueing <managed-jobs>`, any cloud.

            .. grid-item-card::  💰 Cost savings
                :text-align: center

                Cost for the same GPUs varies by up to 4x across clouds - SkyPilot picks the cheapest for you. Run on spot instances to save 3-6x on supported clouds.

            .. grid-item-card::  🚀 Maximum availability
                :text-align: center

                SkyPilot scans all your clouds, regions and Kubernetes clusters for GPU availability. If there's a GPU out there, SkyPilot will find it for you.

            .. grid-item-card::  ☁️ Unified platform for all Infrastructure
                :text-align: center

                Manage all your jobs across all your clouds and Kubernetes clusters in one place. No need to switch between different platforms.


More Information
--------------------------

Tutorials: `SkyPilot Tutorials <https://github.com/skypilot-org/skypilot-tutorial>`_

Runnable examples:

.. Keep this section in sync with README.md in SkyPilot repo

* **LLMs on SkyPilot**

  * `GPT-2 via llm.c <https://github.com/skypilot-org/skypilot/tree/master/llm/gpt-2>`_
  * `Llama 3 <https://github.com/skypilot-org/skypilot/tree/master/llm/llama-3>`_
  * `Qwen <https://github.com/skypilot-org/skypilot/tree/master/llm/qwen>`_
  * `Databricks DBRX <https://github.com/skypilot-org/skypilot/tree/master/llm/dbrx>`_
  * `Gemma <https://github.com/skypilot-org/skypilot/tree/master/llm/gemma>`_
  * `Mixtral 8x7B <https://github.com/skypilot-org/skypilot/tree/master/llm/mixtral>`_; `Mistral 7B <https://docs.mistral.ai/self-deployment/skypilot>`_ (from official Mistral team)
  * `Code Llama <https://github.com/skypilot-org/skypilot/tree/master/llm/codellama/>`_
  * `vLLM: Serving LLM 24x Faster On the Cloud <https://github.com/skypilot-org/skypilot/tree/master/llm/vllm>`_ (from official vLLM team)
  * `SGLang: Fast and Expressive LLM Serving On the Cloud <https://github.com/skypilot-org/skypilot/tree/master//llm/sglang/>`_ (from official SGLang team)
  * `Vicuna chatbots: Training & Serving <https://github.com/skypilot-org/skypilot/tree/master/llm/vicuna>`_ (from official Vicuna team)
  * `Train your own Vicuna on Llama-2 <https://github.com/skypilot-org/skypilot/blob/master/llm/vicuna-llama-2>`_
  * `Self-Hosted Llama-2 Chatbot <https://github.com/skypilot-org/skypilot/tree/master/llm/llama-2>`_
  * `Ollama: Quantized LLMs on CPUs <https://github.com/skypilot-org/skypilot/tree/master/llm/ollama>`_
  * `LoRAX <https://github.com/skypilot-org/skypilot/tree/master/llm/lorax/>`_
  * `QLoRA <https://github.com/artidoro/qlora/pull/132>`_
  * `LLaMA-LoRA-Tuner <https://github.com/zetavg/LLaMA-LoRA-Tuner#run-on-a-cloud-service-via-skypilot>`_
  * `Tabby: Self-hosted AI coding assistant <https://github.com/TabbyML/tabby/blob/bed723fcedb44a6b867ce22a7b1f03d2f3531c1e/experimental/eval/skypilot.yaml>`_
  * `LocalGPT <https://github.com/skypilot-org/skypilot/tree/master/llm/localgpt>`_
  * `Falcon <https://github.com/skypilot-org/skypilot/tree/master/llm/falcon>`_
  * Add yours here & see more in `llm/ <https://github.com/skypilot-org/skypilot/tree/master/llm>`_!

* Framework examples: `PyTorch DDP <https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_torch.yaml>`_, `DeepSpeed <https://github.com/skypilot-org/skypilot/blob/master/examples/deepspeed-multinode/sky.yaml>`_, `JAX/Flax on TPU <https://github.com/skypilot-org/skypilot/blob/master/examples/tpu/tpuvm_mnist.yaml>`_, `Stable Diffusion <https://github.com/skypilot-org/skypilot/tree/master/examples/stable_diffusion>`_, `Detectron2 <https://github.com/skypilot-org/skypilot/blob/master/examples/detectron2_docker.yaml>`_, `Distributed <https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_distributed_tf_app.py>`_ `TensorFlow <https://github.com/skypilot-org/skypilot/blob/master/examples/resnet_app_storage.yaml>`_, `NeMo <https://github.com/skypilot-org/skypilot/blob/master/examples/nemo/nemo_gpt_train.yaml>`_, `programmatic grid search <https://github.com/skypilot-org/skypilot/blob/master/examples/huggingface_glue_imdb_grid_search_app.py>`_, `Docker <https://github.com/skypilot-org/skypilot/blob/master/examples/docker/echo_app.yaml>`_, `Cog <https://github.com/skypilot-org/skypilot/blob/master/examples/cog/>`_, `Unsloth <https://github.com/skypilot-org/skypilot/blob/master/examples/unsloth/unsloth.yaml>`_, `Ollama <https://github.com/skypilot-org/skypilot/blob/master/llm/ollama>`_, `llm.c <https://github.com/skypilot-org/skypilot/tree/master/llm/gpt-2>`__ and `many more <https://github.com/skypilot-org/skypilot/tree/master/examples>`_.

Follow updates:

* `Twitter <https://twitter.com/skypilot_org>`_
* `Slack <http://slack.skypilot.co>`_
* `SkyPilot Blog <https://blog.skypilot.co/>`_ (`Introductory blog post <https://blog.skypilot.co/introducing-skypilot/>`_)

Read the research:

* `SkyPilot paper <https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf>`_ and `talk <https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_ (NSDI 2023)
* `Sky Computing whitepaper <https://arxiv.org/abs/2205.07147>`_
* `Sky Computing vision paper <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ (HotOS 2021)


Contents
--------

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   ../getting-started/installation
   ../getting-started/quickstart
   ../getting-started/tutorial
   ../examples/interactive-development


.. toctree::
   :maxdepth: 1
   :caption: Running Jobs

   ../examples/managed-jobs
   ../reference/job-queue
   ../examples/auto-failover
   ../reference/kubernetes/index
   ../running-jobs/distributed-jobs

.. toctree::
   :maxdepth: 1
   :caption: SkyServe: Model Serving

   ../serving/sky-serve
   ../serving/user-guides
   ../serving/service-yaml-spec

.. toctree::
   :maxdepth: 1
   :caption: Cutting Cloud Costs

   Managed Spot Jobs <../examples/spot-jobs>
   ../reference/auto-stop
   ../reference/benchmark/index

.. toctree::
   :maxdepth: 1
   :caption: Using Data

   ../examples/syncing-code-artifacts
   ../reference/storage

.. toctree::
   :maxdepth: 1
   :caption: User Guides

   ../running-jobs/environment-variables
   ../examples/docker-containers
   ../examples/ports
   ../reference/tpu
   ../reference/logging
   ../reference/faq


.. toctree::
   :maxdepth: 1
   :caption: Developer Guides

   ../developers/CONTRIBUTING
   Guide: Adding a New Cloud <https://docs.google.com/document/d/1oWox3qb3Kz3wXXSGg9ZJWwijoa99a3PIQUHBR8UgEGs/edit?usp=sharing>

.. toctree::
   :maxdepth: 1
   :caption: Cloud Admin and Usage

   ../cloud-setup/cloud-permissions/index
   ../cloud-setup/cloud-auth
   ../cloud-setup/quota

.. toctree::
   :maxdepth: 1
   :caption: References

   ../reference/yaml-spec
   ../reference/cli
   ../reference/api
   ../reference/config


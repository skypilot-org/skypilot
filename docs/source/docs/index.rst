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

- Launch :ref:`dev clusters <dev-cluster>`, :ref:`jobs <managed-jobs>`, and :ref:`serving <sky-serve>` on any infra
- Easy job management: queue, run, and auto-recover many jobs

SkyPilot **supports multiple clusters, clouds, and hardware** (`the Sky <https://arxiv.org/abs/2205.07147>`_):

- Bring your reserved GPUs, Kubernetes clusters, or 12+ clouds
- :ref:`Flexible provisioning <auto-failover>` of GPUs, TPUs, CPUs, with auto-retry

SkyPilot **cuts your cloud costs & maximizes GPU availability**:

* :ref:`Autostop <auto-stop>`: automatic cleanup of idle resources
* :ref:`Managed Spot <managed-jobs>`: 3-6x cost savings using spot instances, with preemption auto-recovery
* :ref:`Optimizer <auto-failover>`: 2x cost savings by auto-picking the cheapest & most available infra

SkyPilot supports your existing GPU, TPU, and CPU workloads, with no code changes.

:ref:`Current supported infra <installation>` (Kubernetes; AWS, GCP, Azure, OCI, Lambda Cloud, Fluidstack, RunPod, Cudo, Paperspace, Cloudflare, Samsung, IBM, VMware vSphere):

.. raw:: html

   <p align="center">
   <picture>
      <img class="only-light" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-light.png" width=85%>
      <img class="only-dark" alt="SkyPilot Supported Clouds" src="https://raw.githubusercontent.com/skypilot-org/skypilot/master/docs/source/images/cloud-logos-dark.png" width=85%>
   </picture>
   </p>

Ready to get started?
----------------------

:ref:`Install SkyPilot <installation>` in ~1 minute. Then, launch your first dev cluster in ~5 minutes in :ref:`Quickstart <quickstart>`.

Everything is launched within your cloud accounts, VPCs, and cluster(s).

Contact the SkyPilot team
---------------------------------

You can chat with the SkyPilot team and community on the `SkyPilot Slack <http://slack.skypilot.co>`_.

Learn more
--------------------------

Runnable examples:

.. Keep this section in sync with README.md in SkyPilot repo

* **LLMs on SkyPilot**

  * `Llama 3.1 finetuning <https://github.com/skypilot-org/skypilot/tree/master/llm/llama-3_1-finetuning>`_ and `serving <https://github.com/skypilot-org/skypilot/tree/master/llm/llama-3_1>`_
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

Case Studies and Integrations: `Community Spotlights <https://blog.skypilot.co/community/>`_

Tutorials: `SkyPilot Tutorials <https://github.com/skypilot-org/skypilot-tutorial>`_

Follow updates:

* `Twitter <https://twitter.com/skypilot_org>`_
* `Slack <http://slack.skypilot.co>`_
* `SkyPilot Blog <https://blog.skypilot.co/>`_ (`Introductory blog post <https://blog.skypilot.co/introducing-skypilot/>`_)

Read the research:

* `SkyPilot paper <https://www.usenix.org/system/files/nsdi23-yang-zongheng.pdf>`_ and `talk <https://www.usenix.org/conference/nsdi23/presentation/yang-zongheng>`_ (NSDI 2023)
* `Sky Computing whitepaper <https://arxiv.org/abs/2205.07147>`_
* `Sky Computing vision paper <https://sigops.org/s/conferences/hotos/2021/papers/hotos21-s02-stoica.pdf>`_ (HotOS 2021)



.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Getting Started

   ../getting-started/installation
   ../getting-started/quickstart
   ../getting-started/tutorial
   ../examples/interactive-development


.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Running Jobs

   ../examples/managed-jobs
   ../reference/job-queue
   ../examples/auto-failover
   ../reference/kubernetes/index
   ../running-jobs/distributed-jobs

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: SkyServe: Model Serving

   ../serving/sky-serve
   ../serving/user-guides
   ../serving/service-yaml-spec

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Cutting Cloud Costs

   Managed Spot Jobs <../examples/spot-jobs>
   ../reference/auto-stop
   ../reference/benchmark/index

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Using Data

   ../examples/syncing-code-artifacts
   ../reference/storage

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: User Guides

   ../running-jobs/environment-variables
   ../examples/docker-containers
   ../examples/ports
   ../reference/tpu
   ../reference/logging
   ../reference/faq
   SkyPilot vs. Other Systems <../reference/comparison>


.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Developer Guides

   ../developers/CONTRIBUTING
   Guide: Adding a New Cloud <https://docs.google.com/document/d/1oWox3qb3Kz3wXXSGg9ZJWwijoa99a3PIQUHBR8UgEGs/edit?usp=sharing>

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Cloud Admin and Usage

   ../cloud-setup/cloud-permissions/index
   ../cloud-setup/cloud-auth
   ../cloud-setup/quota

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: References

   ../reference/yaml-spec
   ../reference/cli
   ../reference/api
   ../reference/config


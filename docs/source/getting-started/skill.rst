.. _skill:

Agent Skills
============

SkyPilot provides an official skill that teaches AI agents (Claude Code,
Codex, etc.) how to use SkyPilot. With it installed, your agent can launch
clusters, run jobs, serve models, and manage cloud resources effectively.

Installation
------------

.. tab-set::

  .. tab-item:: Generic

    Just tell your agent:

    .. code-block:: bash

      Fetch and follow https://github.com/skypilot-org/skypilot/blob/HEAD/agent/INSTALL.md to install the skypilot skill

    You may need to restart the agent to reload the skill after installation.

  .. tab-item:: Claude Code

    Install the SkyPilot skill as a Claude Code plugin:

    .. code-block:: bash

      claude plugin marketplace add skypilot-org/skypilot
      claude plugin install skypilot@skypilot

  .. tab-item:: npx skills

    If you have `npx skills` installed, you can install the skypilot skill with:

    .. code-block:: bash

      npx skills add skypilot-org/skypilot


.. tip::

  The agent will install the SkyPilot CLI and guide you to set up cloud credentials
  when it decides to use SkyPilot to do something. You can also ask it to do this immediately
  by telling it "Bootstrap skypilot".

What you can do
---------------

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Capability
     - Example
   * - Interactive dev environment
     - *"Launch a cluster with H100 GPU, connect my VS Code to it, and set it to auto-stop after 30 min idle."*
   * - Launch dev clusters
     - *"Launch a cluster with 4 A100 GPUs. Install PyTorch and auto-stop it after 30 min idle."*
   * - Fine-tune models
     - *"Fine-tune Llama 3.1 8B on my dataset at s3://my-data/train.jsonl. Use spot instances and recover from preemptions."*
   * - Distributed training
     - *"Run PyTorch DDP training across 4 nodes with 8 H100s each."*
   * - Hyperparameter sweep
     - *"Sweep learning rates [1e-4, 1e-5, 1e-6] in parallel across whatever clouds have availability."*
   * - Serve models
     - *"Deploy Llama 3.1 70B with vLLM. Autoscale from 1 to 3 replicas based on QPS."*
   * - Multi-cloud failover
     - *"Submit training jobs that try our Slurm cluster first and fall back to AWS if it's full."*
   * - Compare GPU pricing
     - *"What's the cheapest 8x H200 across AWS, GCP, Lambda, and CoreWeave?"*
   * - Debug skypilot usage
     - *"My task.yaml gets 'resources not available' errors. Help me debug and fix it."*

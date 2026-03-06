.. _agent-skill:

SkyPilot Skill
==============

SkyPilot provides an official skill that teaches AI agents (Claude Code,
Codex, etc.) how to use SkyPilot. With it installed, your agent can launch
clusters, run jobs, serve models, and manage cloud resources effectively.

Installation
------------

.. tab-set::

  .. tab-item:: Claude Code

    Install the SkyPilot skill as a Claude Code plugin:

    .. code-block:: bash

      claude plugin marketplace add skypilot-org/skypilot
      claude plugin install skypilot@skypilot

  .. tab-item:: npx skills

    If you have `npx skills` installed, you can install the skypilot skill with:

    .. code-block:: bash

      npx skills add skypilot-org/skypilot

  .. tab-item:: Generic

    Just tell your agent:

    .. code-block:: plaintext

      Fetch and follow https://github.com/skypilot-org/skypilot/blob/master/agent/INSTALL.md to install the skypilot skill

    You may need to restart the agent to reload the skill after installation.

.. tip::

  The agent will install the SkyPilot CLI and the guide you to set up cloud credentials
  when it decide to use SkyPilot to do something. You can also ask it to do this immediately
  by telling it "Bootstrap skypilot".

Examples
--------

Here are some examples of what you can ask your agent with the SkyPilot skill installed:

.. dropdown:: "Launch a cluster with 4 A100 GPUs for interactive development. Install PyTorch and set autostop to 2 hours."

    The agent writes a task YAML with ``accelerators: A100:4``, adds a setup
    command for PyTorch installation, and runs ``sky launch`` with
    ``--idle-minutes-to-autostop 120``.

.. dropdown:: "Set up a managed job to fine-tune Llama 3.1 8B on my dataset at s3://my-data/train.jsonl. Use spot instances. Make sure it can recover from preemptions."

    The agent creates a managed job YAML with spot instance configuration,
    sets up file mounts to access your S3 data, and launches the job with
    ``sky jobs launch`` using automatic preemption recovery.

.. dropdown:: "Deploy Llama 3.1 70B with vLLM using SkyServe. Set up autoscaling from 1 to 3 replicas based on QPS."

    The agent writes a SkyServe YAML with a vLLM service, configures the
    ``service`` section with ``min_replicas: 1`` and ``max_replicas: 3``,
    and deploys it with ``sky serve up``.

.. dropdown:: "What's the cheapest way to get 8 H100 GPUs? Show me options across clouds."

    The agent runs ``sky show-gpus H100 --all`` to display pricing and
    availability across all configured clouds, then summarizes the cheapest
    options.

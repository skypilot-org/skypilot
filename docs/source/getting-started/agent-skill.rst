.. _agent-skill:

SkyPilot Skill
==============

SkyPilot provides an official skill that teaches AI coding agents (Claude Code,
Cursor, Windsurf, etc.) how to use SkyPilot effectively. With it installed, your
agent can launch clusters, write task YAMLs, run jobs, serve models, and manage
cloud resources — using the same SkyPilot CLI and SDK you already know.

Why use the SkyPilot Skill?
---------------------------

- **Instant expertise**: Agent learns SkyPilot's CLI, YAML spec, SDK, and best practices
- **Avoids common mistakes**: Built-in guidance prevents hardcoding clouds, forgetting cleanup, etc.
- **Full workflow coverage**: Clusters, managed jobs, SkyServe, distributed training, spot instances
- **Always up to date**: Includes reference docs for CLI flags, YAML fields, SDK methods

Installation
------------

.. tab-set::

  .. tab-item:: Claude Code (Recommended)

    Install the SkyPilot skill as a Claude Code plugin:

    .. code-block:: bash

      claude plugin add skypilot-org/skypilot
      claude install-plugin skypilot-org/skypilot

  .. tab-item:: Claude Code (Manual)

    Clone the SkyPilot repo and copy the skill into your project:

    .. code-block:: bash

      git clone https://github.com/skypilot-org/skypilot.git
      cp -r skypilot/skills/skypilot/skills/skypilot .claude/skills/

  .. tab-item:: Other Agents (Cursor, Windsurf, Copilot)

    Copy the content of `SKILL.md <https://github.com/skypilot-org/skypilot/blob/master/skills/skypilot/skills/skypilot/SKILL.md>`_ into your agent's custom instructions file:

    - **Cursor**: ``.cursor/rules/skypilot.md``
    - **Windsurf**: ``.windsurfrules`` or project rules
    - **GitHub Copilot**: ``.github/copilot-instructions.md``

.. tip::

  The skill teaches SkyPilot-specific knowledge. The agent will install the
  SkyPilot CLI if it's not already present. You still need cloud credentials
  configured — the agent will guide you through ``sky check`` to verify.

Example Prompts
---------------

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

What the Skill Includes
-----------------------

- **Core knowledge** (``SKILL.md``): When to use clusters vs managed jobs vs SkyServe, YAML structure, GPU/cloud selection, common workflows
- **CLI Reference**: All ``sky`` commands, flags, and usage patterns
- **YAML Specification**: Complete task YAML field reference
- **Python SDK**: Programmatic API for launching and managing resources
- **Advanced Patterns**: Distributed training, spot strategies, multi-cloud setups
- **Examples**: Real-world templates for training, serving, and batch inference
- **Troubleshooting**: Common errors and their solutions

How the Skill Works
-------------------

The skill is a structured knowledge base that the agent reads — it is not
executable code. When the agent encounters a SkyPilot-related task, it consults
the skill files to understand the correct CLI commands, YAML syntax, and best
practices.

Browse the skill files on GitHub: `skills/skypilot/ <https://github.com/skypilot-org/skypilot/tree/master/skills/skypilot>`_

See also
--------

- :ref:`Quickstart <quickstart>`
- :ref:`Task YAML reference <yaml-spec>`
- :ref:`CLI reference <cli>`
- :ref:`Python SDK <pythonapi>`

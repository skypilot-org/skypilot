.. _lifecycle-hooks:
.. _auto-stop-hooks:

Lifecycle hooks
===============

Lifecycle hooks let a SkyPilot task run scripts on the cluster in response to
**autostop**, **preemption**, or **down** (``sky down``) events. Each hook
script runs on the remote cluster before the cluster is stopped or torn down,
with access to the cluster's filesystem and environment variables.

Common use cases include committing code, saving checkpoints, uploading logs,
sending notifications, and performing cleanup before the cluster goes away.

Quick start
-----------

Add a ``hooks:`` list under the task YAML's ``config:`` block. Each entry
runs on the cluster on lifecycle events — ``autostop``, ``preemption``,
or ``down``:

.. code-block:: yaml

   config:
     hooks:
       - run: |
           cd my-code-base
           git add .
           git commit -m "Commit my code"
           git push

By default a hook fires on all three events. See :ref:`Schema
<lifecycle-hooks-schema>` below to scope it to a subset and set a custom
timeout.

.. _lifecycle-hooks-schema:

Schema
------

Each entry under ``config.hooks`` is an object with three fields:

- ``run`` (required): The shell script to execute on the cluster.
- ``events`` (optional, default ``[autostop, preemption, down]``): The list of
  lifecycle events that trigger this hook. Each item must be one of
  ``autostop``, ``preemption``, ``down``.
- ``timeout`` (optional, default ``3600`` seconds, minimum ``1``): Maximum
  number of seconds the hook is allowed to run. On timeout, the hook is
  terminated and the lifecycle event proceeds.

Multiple hook entries are run in declaration order for each matching event.
If a hook exits non-zero or times out, the lifecycle event still proceeds and
a warning is logged.

Events
------

- ``autostop``: Fires when the cluster's idle timer elapses
  (``resources.autostop`` / ``sky autostop -i N``). Runs on the head node
  before stop or tear-down. See :ref:`auto-stop`.
- ``preemption``: Fires when the cluster receives a termination signal from
  the cloud or Kubernetes (e.g., spot reclamation, node drain). On
  Kubernetes, SkyPilot automatically extends the pod's
  ``terminationGracePeriodSeconds`` to fit the longest preemption-hook
  ``timeout``.
- ``down``: Fires when ``sky down <cluster>`` is invoked. Runs on the head
  node before the cluster is torn down.

.. note::

   Hook execution keeps the cluster alive while it runs, occupying the
   resources. Pick a ``timeout`` that fits your workload and the cloud's
   preemption notice window (typically 30–120 seconds for spot VMs).

Viewing hook logs
-----------------

Each event writes its output to ``~/.sky/hooks/<event>.log`` on the cluster.
Use ``sky logs --hook`` to stream them:

.. code-block:: bash

   # Stream the autostop hook log
   sky logs --hook autostop mycluster

   # Auto-select whichever log exists (e.g., after a preemption)
   sky logs --hook mycluster

The ``sky logs --autostop`` flag is kept as a deprecated alias for
``sky logs --hook autostop``.

Examples
--------

.. dropdown:: Committing and pushing code changes (autostop)

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
       config:
         hooks:
           - run: |
               cd my-code-base
               git add .
               git commit -m "Auto-commit before shutdown"
               git push
             events: [autostop]

.. dropdown:: Saving model checkpoints to persistent storage (autostop + preemption)

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Save checkpoints to a mounted volume or cloud storage
               cp -r /workspace/checkpoints/* /mnt/persistent-storage/checkpoints/
               # Or upload to S3
               aws s3 sync /workspace/checkpoints/ s3://my-bucket/checkpoints/
             events: [autostop, preemption]
             timeout: 120

.. dropdown:: Uploading logs or results to cloud storage

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Upload logs to S3
               aws s3 sync /workspace/logs/ s3://my-bucket/logs/$(date +%Y%m%d)/
               # Or upload to GCS
               gcloud storage cp -r /workspace/results/ gs://my-bucket/results/$(date +%Y%m%d)/

.. dropdown:: Syncing W&B runs before shutdown

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Sync W&B runs to the cloud before shutdown
               wandb sync ./wandb
               # Or sync a specific run
               # wandb sync ./wandb/run-20250813_124246-n67z9ude
             events: [autostop, preemption]

.. dropdown:: Sending notifications about cluster shutdown

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Send email notification
               echo "Cluster shutting down" | \
                 mail -s "Cluster Autostop" user@example.com
               # Or send Slack notification via webhook
               curl -X POST -H 'Content-type: application/json' \
                 --data '{"text":"Cluster shutting down"}' \
                 https://hooks.slack.com/services/YOUR/WEBHOOK/URL

.. dropdown:: Triggering downstream workflows

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Trigger an evaluation pipeline in Airflow
               curl -X POST https://airflow.example.com/api/v1/dags/model_eval/dag_runs \
                    -H "Content-Type: application/json" \
                    -d '{"conf": {"model_path": "s3://my-bucket/models/v1"}}'
             events: [autostop]

.. dropdown:: Pushing model to Hugging Face Hub

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Upload the trained model to Hugging Face Hub
               huggingface-cli upload my-org/my-model /workspace/model-output .
             events: [autostop]

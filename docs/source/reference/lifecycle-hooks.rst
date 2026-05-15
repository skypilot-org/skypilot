.. _lifecycle-hooks:
.. _auto-stop-hooks:

Lifecycle hooks
===============

Lifecycle hooks let a SkyPilot task run scripts on the cluster in response to
**stop**, **preemption**, or **down** events. Each hook script runs on the
remote cluster before the cluster is stopped or torn down, with access to the
cluster's filesystem and environment variables.

Common use cases include committing code, saving checkpoints, uploading logs,
sending notifications, and performing cleanup before the cluster goes away.

Quick start
-----------

Add a ``hooks:`` list under the task YAML's ``config:`` block. Each entry
runs on the cluster on lifecycle events — ``stop``, ``preemption``,
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
- ``events`` (optional, default ``[stop, preemption, down]``): The list of
  lifecycle events that trigger this hook. Each item must be one of
  ``stop``, ``preemption``, ``down``.
- ``timeout`` (optional, default ``3600`` seconds, minimum ``1``): Maximum
  number of seconds the hook is allowed to run. On timeout, the hook is
  terminated and the lifecycle event proceeds.

Multiple hook entries are run in declaration order for each matching event.
If a hook exits non-zero or times out, the lifecycle event still proceeds and
a warning is logged.

Events
------

Events are named by the outcome (what's about to happen to the cluster), not
by the trigger. Hook authors choose the event by what they want their script
to react to, regardless of which mechanism caused the teardown.

- ``stop``: Fires when the cluster is being stopped (disks preserved) —
  either by ``sky stop <cluster>`` or by the idle timer
  (``resources.autostop`` / ``sky autostop -i N`` with ``down: false``).
  Runs on the head node before the cluster is stopped. See :ref:`auto-stop`.
- ``preemption``: Fires when the cluster receives a termination signal from
  the cloud or Kubernetes (e.g., spot reclamation, node drain). On
  Kubernetes, SkyPilot automatically extends the pod's
  ``terminationGracePeriodSeconds`` to fit the longest preemption-hook
  ``timeout``.
- ``down``: Fires when the cluster is being torn down — either by
  ``sky down <cluster>`` or by the idle timer with autodown enabled
  (``autostop.down: true``). Runs on the head node before the cluster is
  torn down.

Environment variables
~~~~~~~~~~~~~~~~~~~~~

Each hook script sees ``SKYPILOT_HOOK_EVENT`` set to the firing event
(``stop`` / ``preemption`` / ``down``). A single hook that defaults to
all three events (no ``events:`` key) can branch on it instead of
requiring three separate entries:

.. code-block:: yaml

   config:
     hooks:
       - run: |
           case "$SKYPILOT_HOOK_EVENT" in
             preemption) ./fast_save.sh ;;
             stop|down)  ./full_save.sh && ./git_commit.sh ;;
           esac

.. note::

   Hook execution keeps the cluster alive while it runs, occupying the
   resources. Pick a ``timeout`` that fits your workload and the cloud's
   preemption notice window (typically 30–120 seconds for spot VMs).

Viewing hook logs
-----------------

Each event writes its output to ``~/.sky/hooks/<event>.log`` on the cluster.
Use ``sky logs --hook`` to stream them:

.. code-block:: bash

   # Stream the stop hook log
   sky logs --hook stop mycluster

   # Auto-select whichever log exists (e.g., after a preemption)
   sky logs --hook mycluster

Examples
--------

.. dropdown:: Committing and pushing code changes (stop)

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
             events: [stop]

.. dropdown:: Saving model checkpoints to persistent storage (stop + preemption)

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Save checkpoints to a mounted volume or cloud storage
               cp -r /workspace/checkpoints/* /mnt/persistent-storage/checkpoints/
               # Or upload to S3
               aws s3 sync /workspace/checkpoints/ s3://my-bucket/checkpoints/
             events: [stop, preemption]
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
             events: [stop, preemption]

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
             events: [stop]

.. dropdown:: Pushing model to Hugging Face Hub

    .. code-block:: yaml

       config:
         hooks:
           - run: |
               # Upload the trained model to Hugging Face Hub
               huggingface-cli upload my-org/my-model /workspace/model-output .
             events: [stop]

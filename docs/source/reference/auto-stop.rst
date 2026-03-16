.. _auto-stop:

Autostop and Autodown
============================

The **autostop** (or **autodown**) feature automatically stops (or tears down) a
cluster after it becomes :ref:`idle <auto-stop-setting-idleness-behavior>`.

With autostop, users can simply submit jobs and leave their laptops, while
ensuring no unnecessary spending occurs. After jobs have finished, the
clusters used will be automatically stopped, which can be restarted later.

With autodown, the clusters used will be automatically torn down, i.e.,
terminated.

.. note::

  The autostop/autodown logic is executed by the remote cluster.  Your local
  machine does *not* need to stay up for them to take effect.

Setting autostop
~~~~~~~~~~~~~~~~

To schedule autostop for a cluster, set autostop in the SkyPilot YAML:

.. code-block:: yaml

   resources:
     autostop: true  # Stop after default idle minutes (5).

     # Or:
     autostop: 10m  # Stop after this many idle minutes.

     # Or:
     autostop:
       idle_minutes: 10

Alternatively, use :code:`sky autostop` or ``sky launch -i <idle minutes>``:

.. code-block:: bash

   # Launch a cluster with logging detached (the -d flag)
   sky launch -d -c mycluster cluster.yaml

   # Autostop the cluster after 10 minutes of idleness
   sky autostop mycluster -i 10

   # Use the default, 5 minutes of idleness
   # sky autostop mycluster

   # (Equivalent to the above) Use the -i flag:
   sky launch -d -c mycluster cluster.yaml -i 10


Setting autodown
~~~~~~~~~~~~~~~~

To schedule autodown for a cluster, set autodown in the SkyPilot YAML:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 10
       down: true  # Use autodown.

Alternatively, pass the ``--down`` flag to either :code:`sky autostop` or ``sky launch``:

.. code-block:: bash

   # Add the --down flag to schedule autodown instead of autostop.

   # This means the cluster will be torn down after 10 minutes of idleness.
   sky launch -d -c mycluster2 cluster.yaml -i 10 --down

   # Or:
   sky autostop mycluster2 -i 10 --down


Canceling autostop/autodown
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To cancel any scheduled autostop/autodown on the cluster:

.. code-block:: bash

   sky autostop mycluster --cancel

Viewing autostop status
~~~~~~~~~~~~~~~~~~~~~~~

To view the status of the cluster, use ``sky dashboard`` or ``sky status``:

.. code-block:: bash

   $ sky status
   NAME         INFRA           RESOURCES                     STATUS   AUTOSTOP       LAUNCHED
   mycluster    AWS (us-east-1) 2x(cpus=8, m4.2xlarge, ...)   UP       10 min         1 min ago
   mycluster2   AWS (us-east-1) 2x(cpus=8, m4.2xlarge, ...)   UP       10 min(down)   1 min ago

Clusters that are autostopped/autodowned are automatically removed from the status table.

.. _auto-stop-setting-idleness-behavior:

Setting idleness behavior
~~~~~~~~~~~~~~~~~~~~~~~~~~

A cluster is considered idle if there are no in‑progress jobs (pending or running)
and no active SSH sessions. You can change the idleness criteria in SkyPilot YAML with
:ref:`resources.autostop.wait_for <yaml-spec-resources-autostop>`.

``wait_for`` can be set as one of the followings:

- ``jobs_and_ssh`` (default): Wait for in‑progress jobs and SSH connections to finish.
- ``jobs``: Only wait for in‑progress jobs — useful for ignoring long‑running SSH or IDE connections.
- ``none``: Wait for nothing; autostop right after ``idle_minutes`` — useful for ignoring long‑running jobs (e.g., Jupyter notebooks) and enforcing a hard time limit.

Examples:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 10
       wait_for: jobs_and_ssh

Alternatively, pass the ``--wait-for`` flag to either ``sky autostop`` or ``sky launch``:

.. code-block:: bash

   # Default: Running jobs and active SSH sessions reset the idleness timer.
   sky launch -d -c mycluster cluster.yaml -i 10 --wait-for jobs_and_ssh

   # Or:
   sky autostop mycluster -i 10 --wait-for jobs_and_ssh

   # Only running jobs reset the idleness timer.
   sky autostop mycluster -i 10 --wait-for jobs

   # Hard time limit: Stop after 10 minutes, regardless of running jobs or SSH sessions.
   sky autostop mycluster -i 10 --wait-for none

.. _auto-stop-hooks:

Autostop hooks
~~~~~~~~~~~~~~

To execute a script before autostopping, specify a hook in the autostop configuration.
The hook script runs on the remote cluster before the cluster is stopped or torn down.
This is useful for tasks like committing code, saving checkpoints, or performing cleanup operations.

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 10
       hook: |
         cd my-code-base
         git add .
         git commit -m "Commit my code"
         git push
       hook_timeout: 300

The hook script runs on the cluster and has access to the cluster's filesystem and environment variables.
If the hook script fails (non-zero exit code), the autostop process will still continue,
but a warning will be logged.

**Hook Timeout**

By default, autostop hooks have a **1-hour (3600 seconds) timeout**. If your hook
takes longer than this, it will be killed and autostop will proceed. To
customize the timeout in your YAML configuration:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 10
       hook: |
         # Long-running backup operation
         tar -czf backup.tar.gz /large/dataset
         aws s3 cp backup.tar.gz s3://my-bucket/
       hook_timeout: 7200  # 2 hours in seconds

**Important Notes:**

- If the hook times out, autostop will proceed after logging a warning
- The minimum timeout is 1 second
- Hook execution will keep the cluster from terminating while it runs, occupying the resources. Be aware of that when setting ``idle_minutes``

Common use cases for autostop hooks:

.. dropdown:: Committing and pushing code changes

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             cd my-code-base
             git add .
             git commit -m "Auto-commit before shutdown"
             git push

.. dropdown:: Saving model checkpoints to persistent storage

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             # Save checkpoints to a mounted volume or cloud storage
             cp -r /workspace/checkpoints/* /mnt/persistent-storage/checkpoints/
             # Or upload to S3
             aws s3 sync /workspace/checkpoints/ s3://my-bucket/checkpoints/

.. dropdown:: Uploading logs or results to cloud storage

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             # Upload logs to S3
             aws s3 sync /workspace/logs/ s3://my-bucket/logs/$(date +%Y%m%d)/
             # Or upload to GCS
             gcloud storage cp -r /workspace/results/ gs://my-bucket/results/$(date +%Y%m%d)/

.. dropdown:: Syncing W&B runs before shutdown

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             # Sync W&B runs to the cloud before shutdown
             # Sync all runs in the wandb directory
             wandb sync ./wandb
             # Or sync a specific run
             # wandb sync ./wandb/run-20250813_124246-n67z9ude

.. dropdown:: Sending notifications about the cluster shutdown

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             # Send email notification
             echo "Cluster shutting down after idle period" | \
               mail -s "Cluster Autostop" user@example.com
             # Or send Slack notification via webhook
             curl -X POST -H 'Content-type: application/json' \
               --data '{"text":"Cluster shutting down after idle period"}' \
               https://hooks.slack.com/services/YOUR/WEBHOOK/URL

.. dropdown:: Triggering downstream workflows

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             # Trigger an evaluation pipeline in Airflow
             curl -X POST https://airflow.example.com/api/v1/dags/model_eval/dag_runs \
                  -H "Content-Type: application/json" \
                  -d '{"conf": {"model_path": "s3://my-bucket/models/v1"}}'

.. dropdown:: Pushing model to Hugging Face Hub

    .. code-block:: yaml

       resources:
         autostop:
           idle_minutes: 10
           hook: |
             # Upload the trained model to Hugging Face Hub
             huggingface-cli upload my-org/my-model /workspace/model-output .

.. _auto-stop-preemption-hooks:

Preemption hooks
~~~~~~~~~~~~~~~~

When using **spot (preemptible) instances**, the autostop hook also runs when
the cloud provider is about to reclaim the instance.  This gives your hook a
chance to checkpoint or clean up before the instance dies.

No extra configuration is needed — if you have an autostop hook configured and
your cluster uses spot instances, SkyPilot will automatically attempt to run the
same hook script on preemption.

**How it works**

- **AWS**: SkyPilot polls the `EC2 instance metadata service
  <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html>`_
  every 5 seconds.  AWS provides ~2 minutes of advance warning before reclaiming
  a spot instance.
- **GCP / Azure**: SkyPilot catches the ``SIGTERM`` signal sent by
  the cloud before termination.  The grace period is typically ~30 seconds.
- **Kubernetes**: Preemption hooks are **not supported**. On Kubernetes the
  container entrypoint traps ``SIGTERM`` to keep pods alive for HA recovery,
  so the termination signal never reaches the skylet process.

**Automatic timeout capping**

Because the cloud enforces a hard deadline, SkyPilot automatically caps the hook
timeout to the available grace period:

.. list-table::
   :header-rows: 1

   * - Cloud
     - Grace period
     - Effective hook timeout
   * - AWS
     - ~2 min
     - ``min(hook_timeout, 110s)``
   * - GCP
     - ~30 s
     - ``min(hook_timeout, 25s)``
   * - Azure
     - ~30 s
     - ``min(hook_timeout, 25s)``
   * - Kubernetes
     - N/A
     - Not supported

If the timeout is capped, a warning is logged.  The hook's ``hook_timeout``
setting still applies for normal autostop (idle timeout) where there is no
external deadline.

**Recommendations**

- Keep preemption hooks fast — use them for checkpointing, not long operations.
- On GCP/Azure the ~25 s budget is tight; prefer a simple ``cp`` or
  ``aws s3 cp`` over a full ``rsync``.
- If both the metadata poller (AWS) and ``SIGTERM`` fire, SkyPilot guarantees the
  hook runs at most once.

.. _slurm-to-skypilot:

Migrating from Slurm to SkyPilot
================================

This guide helps users familiar with Slurm transition to SkyPilot. It covers command mappings, environment variables, script porting, and common workflow patterns.

Why use SkyPilot instead of Slurm?
----------------------------------

TODO - isolation, multi-cluster, ...?

Slurm to SkyPilot
-----------------

Most Slurm concepts map directly to SkyPilot concepts.

.. list-table::
   :widths: 25 40 35
   :header-rows: 1

   * - Slurm
     - SkyPilot
     - Notes
   * - ``salloc --gpus=8``
     - ``sky launch -c dev --gpus H100:8`` then ``ssh dev``
     - Interactive allocation
   * - ``salloc`` + ``srun``
     - ``sky launch -c dev task.yaml``
     - Allocate then run commands
   * - ``srun <command>``
     - ``sky exec <cluster> <command>``
     - Run command on existing cluster
   * - ``squeue``
     - ``sky status``
     - View running clusters and jobs
   * - ``exit`` (from salloc) or ``scancel <alloc_id>``
     - ``sky down <cluster>``
     - Terminate cluster/release allocation
   * - ``sbatch script.sh``
     - ``sky jobs launch task.yaml``
     - Submit a batch job
   * - ``scancel <jobid>``
     - ``sky jobs cancel <job_id>``
     - Cancel a job
   * - ``sacct``
     - ``sky jobs queue``
     - View job history
   * - ``sinfo``
     - ``sky show-gpus``
     - View available resources

Environment Variable Mapping
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot exposes environment variables similar to Slurm for distributed jobs. See :ref:`sky-env-vars` for full details.

.. list-table::
   :widths: 30 30 40
   :header-rows: 1

   * - Slurm
     - SkyPilot
     - Notes
   * - ``$SLURM_JOB_NODELIST``
     - ``$SKYPILOT_NODE_IPS``
     - Newline-separated list of node IPs
   * - ``$SLURM_NNODES``
     - ``$SKYPILOT_NUM_NODES``
     - Total number of nodes
   * - ``$SLURM_NODEID`` / ``$SLURM_PROCID``
     - ``$SKYPILOT_NODE_RANK``
     - Node rank (0 to N-1)
   * - ``$SLURM_GPUS_PER_NODE``
     - ``$SKYPILOT_NUM_GPUS_PER_NODE``
     - Number of GPUs per node
   * - ``$SLURM_JOB_ID``
     - ``$SKYPILOT_TASK_ID``
     - Unique job identifier

Example usage in a distributed training script:

.. code-block:: yaml

   num_nodes: 2

   resources:
     accelerators: H100:8

   run: |
     HEAD_IP=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
     if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
       echo "I am the head node at $HEAD_IP"
     else
       echo "I am worker $SKYPILOT_NODE_RANK, connecting to $HEAD_IP"
     fi

Porting sbatch Scripts to SkyPilot YAML
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Here's a side-by-side comparison of a typical Slurm script and its SkyPilot equivalent:

.. raw:: html

   <div class="row">
       <div class="col-md-6 mb-3">
            <h4> Slurm sbatch script </h4>

.. code-block:: bash

   #!/bin/bash
   #SBATCH --job-name=train
   #SBATCH --nodes=2
   #SBATCH --gpus-per-node=8
   #SBATCH --cpus-per-task=32
   #SBATCH --mem=256G
   #SBATCH --partition=gpu

   module load cuda/12.1
   source ~/venv/bin/activate

   srun python train.py --epochs 100

.. raw:: html

       </div>
       <div class="col-md-6 mb-3">
            <h4> SkyPilot YAML </h4>

.. code-block:: yaml

   name: train

   num_nodes: 2

   resources:
     accelerators: H100:8
     cpus: 32+
     memory: 256+
     image_id: docker:nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

   setup: |
     pip install torch transformers

   run: |
     python train.py --epochs 100

.. raw:: html

       </div>
   </div>

Key differences:

- **No module system**: Use ``setup:`` for environment configuration (pip, conda) or Docker images
- **Time limits are optional**: SkyPilot uses :ref:`autostop <auto-stop>` for auto-termination. Can be configured to terminate on idleness or wall-clock time.
- **Simpler syntax**: Resource requirements are declarative YAML fields
- **Native container support**: Easily use :ref:`containers <docker-containers>` by setting ``image_id``.

Resource Requests
~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 30 30 40
   :header-rows: 1

   * - Slurm
     - SkyPilot
     - Notes
   * - ``--mem=64G``
     - ``memory: 64+``
     - Minimum memory in GB
   * - ``--cpus-per-task=4``
     - ``cpus: 4+``
     - Minimum vCPUs
   * - ``--gpus-per-node=8``
     - ``accelerators: H100:8``
     - GPU type and count
   * - ``--time=24:00:00``
     - ``autostop: 60m``
     - Idle-based timeout

Example with resource constraints:

.. code-block:: yaml

   resources:
     accelerators: A100:4
     cpus: 16+
     memory: 128+
     disk_size: 500  # GB
     autostop:
       idle_minutes: 30

Interactive Jobs
~~~~~~~~~~~~~~~~

Slurm's ``salloc`` provides an interactive allocation. In SkyPilot, launch a cluster without a ``run`` command and SSH into it:

.. code-block:: bash

   # Launch a cluster with GPUs
   sky launch -c dev --gpus H100:8

   # SSH into the cluster
   ssh dev

   # Or use VSCode Remote-SSH
   code --remote ssh-remote+dev /path/to/code

For multi-node interactive clusters:

.. code-block:: bash

   # Launch 4-node cluster
   sky launch -c dev --gpus H100:8 --num-nodes 4

   # SSH to head node
   ssh dev

   # SSH to worker nodes
   ssh dev-worker1
   ssh dev-worker2
   ssh dev-worker3

When done, terminate with ``sky down dev`` or let ``autostop`` clean up idle clusters.

Job Logs
~~~~~~~~

Slurm writes job output to ``slurm-<jobid>.out``. SkyPilot provides several ways to access logs:

**For clusters (``sky launch``):**

.. code-block:: bash

   sky logs mycluster           # Stream logs in real-time
   sky logs mycluster 2         # View logs for job ID 2 on cluster

**For managed jobs (``sky jobs launch``):**

.. code-block:: bash

   sky jobs logs <job_id>       # Stream logs for a managed job

**Logs location on the cluster:**

Logs are stored at ``~/sky_logs/`` on the cluster, organized by task ID.

**Dashboard:**

The :ref:`SkyPilot dashboard <dashboard>` provides a web UI to view all logs across clusters and jobs.


Job Arrays and Parameter Sweeps
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Slurm job arrays (``sbatch --array=1-100``) allow running many similar jobs with different parameters.

In SkyPilot, use :ref:`managed jobs <managed-jobs>` with environment variables:

.. code-block:: bash

   # Launch 100 jobs with different TASK_ID values
   for i in $(seq 1 100); do
     sky jobs launch --env TASK_ID=$i -y -d task.yaml
   done

Your task YAML can use ``TASK_ID`` to vary behavior:

.. code-block:: yaml

   envs:
     TASK_ID: null  # Required, passed via --env

   run: |
     echo "Running task $TASK_ID"
     python train.py --seed $TASK_ID

For hyperparameter sweeps, you can also pass multiple environment variables:

.. code-block:: bash

   for lr in 0.001 0.01 0.1; do
     for batch in 32 64 128; do
       sky jobs launch --env LR=$lr --env BATCH=$batch -y -d task.yaml
     done
   done

Module System Alternative
~~~~~~~~~~~~~~~~~~~~~~~~~

Slurm clusters often use environment modules (``module load cuda``). With SkyPilot, you have several alternatives:

**Use setup commands:**

.. code-block:: yaml

   setup: |
     pip install torch==2.1.0
     pip install -r requirements.txt

**Use Docker images:**

.. code-block:: yaml

   resources:
     image_id: docker:pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

   run: |
     python train.py

**Use conda environments:**

.. code-block:: yaml

   setup: |
     conda create -n myenv python=3.10 -y
     conda activate myenv
     conda install pytorch pytorch-cuda=12.1 -c pytorch -c nvidia -y

   run: |
     conda activate myenv
     python train.py

Login Node
~~~~~~~~~~

Slurm clusters have login nodes for submitting jobs and accessing shared storage. With SkyPilot:

- **No login node required**: Run ``sky launch`` directly from your laptop
- **For interactive work**: SSH into your cluster after launching (``ssh mycluster``)
- **For batch workflows**: Use :ref:`managed jobs <managed-jobs>` (``sky jobs launch``) which don't require a persistent cluster


Identity and Authentication
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Slurm tracks users by their Unix username. SkyPilot uses :ref:`SSO authentication <api-server-oauth>` (Okta, Google Workspace, Microsoft Entra ID) with the :ref:`SkyPilot API server <sky-api-server>`. User identity is tied to their SSO email, providing:

- Mapping of cluster and job ownership
- Audit logs of who launched what
- Role-based access control (RBAC)

Kubernetes-Specific Topics
--------------------------

The following sections cover topics specific to running SkyPilot on Kubernetes.

Shared Storage on Kubernetes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. important::

   Unlike Slurm clusters that typically have a shared NFS home directory mounted on all nodes, Kubernetes does not automatically mount home directories.

Here are the recommended approaches for shared storage on Kubernetes:

**Option 1: Mount NFS via pod_config**

If your Kubernetes cluster has access to an NFS server (e.g., already mounted on nodes), mount it to your pods:

.. code-block:: yaml

   resources:
     infra: kubernetes

   run: |
     ls -la /mnt/shared
     python train.py --data /mnt/shared/datasets

   config:
     kubernetes:
       pod_config:
         spec:
           containers:
             - volumeMounts:
                 - mountPath: /mnt/shared
                   name: nfs-volume
           volumes:
             - name: nfs-volume
               nfs:
                 server: nfs.example.com
                 path: /exports/shared

To apply this globally to all jobs, add the ``config`` section to your ``~/.sky/config.yaml``.

**Option 2: SkyPilot Volumes (PVCs)**

Create a shared volume using SkyPilot's :ref:`volumes <volumes-on-kubernetes>` feature:

.. code-block:: bash

   # Create volume
   sky volumes apply -f - <<EOF
   name: shared-data
   type: k8s-pvc
   infra: kubernetes
   size: 100Gi
   config:
     access_mode: ReadWriteMany
   EOF

   # Mount in your task
   cat > task.yaml <<EOF
   volumes:
     /mnt/data: shared-data

   run: |
     ls /mnt/data
   EOF

   sky launch task.yaml

**Option 3: Cloud Buckets**

Use cloud object storage for data that doesn't require POSIX semantics:

.. code-block:: yaml

   file_mounts:
     /data:
       source: s3://my-bucket/datasets
       mode: MOUNT

   run: |
     python train.py --data /data

**Option 4: Sync Code with workdir**

For syncing your local code to the cluster, use ``workdir``:

.. code-block:: yaml

   workdir: ./my-project

   run: |
     # Code is synced to ~/sky_workdir/
     python train.py

Partitions and Queues on Kubernetes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Slurm uses partitions (``--partition=gpu``) to direct jobs to specific resources. In SkyPilot on Kubernetes, you can target specific Kubernetes contexts or namespaces.

**Via CLI:**

.. code-block:: bash

   sky launch --infra kubernetes/my-gpu-context task.yaml

**Via YAML:**

.. code-block:: yaml

   resources:
     infra: kubernetes/gpu-context

**Using multiple contexts:**

Configure allowed contexts in ``~/.sky/config.yaml``:

.. code-block:: yaml

   kubernetes:
     allowed_contexts:
       - cpu-context
       - gpu-context
       - high-memory-context

Then SkyPilot's optimizer will choose the best context based on your resource requirements.

Priorities and Quotas on Kubernetes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For advanced scheduling similar to Slurm's fair-share and priority systems:

- **Kueue integration**: SkyPilot supports :ref:`Kueue <kubernetes-kueue>` for queue-based scheduling and quotas
- **Dynamic Workload Scheduler (DWS)**: For workloads requiring capacity reservations

These features allow cluster admins to implement fair-share policies, user quotas, and priority-based scheduling similar to Slurm.


Further Reading
---------------

- :ref:`Distributed jobs <dist-jobs>`: Multi-node training guide
- :ref:`Managed jobs <managed-jobs>`: Fault-tolerant batch jobs
- :ref:`Volumes <volumes-on-kubernetes>`: Persistent storage on Kubernetes
- :ref:`Environment variables <sky-env-vars>`: Full list of SkyPilot environment variables
- :ref:`Training guide <training-guide>`: Best practices for distributed training


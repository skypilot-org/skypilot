.. _pbs-getting-started:

Getting Started on PBS Pro
==========================

Quickstart
----------
Have SSH access to a PBS Pro or OpenPBS cluster? Get started with SkyPilot in 3 steps:

.. code-block:: bash

   # 1. Configure your PBS cluster in ~/.pbs/config
   $ mkdir -p ~/.pbs && cat > ~/.pbs/config << EOF
   Host mycluster
       HostName login.mycluster.myorg.com
       User myusername
       IdentityFile ~/.ssh/id_rsa
   EOF

   # 2. Verify SkyPilot detects your PBS cluster
   $ sky check pbs
   # Shows "PBS: enabled"

   # 3. Launch your first SkyPilot task
   $ sky launch --infra pbs --gpus A100:1 -- nvidia-smi

For detailed instructions, prerequisites, and advanced features, read on.

Prerequisites
-------------

To use a PBS cluster, SkyPilot needs SSH access to a login node where the PBS
client commands (``qsub``, ``qstat``, ``qdel``, ``qselect``, ``pbsnodes``) work.

On the cluster itself, SkyPilot needs:

- A **shared filesystem** (NFS, Lustre, GPFS, ...) visible from the login node
  and every compute node. SkyPilot keeps each cluster's working directory there,
  under ``~/.sky_clusters/<cluster>`` by default (see ``workdir`` below).
- The **OpenSSH server binary** (``sshd``) on the compute nodes. SkyPilot runs a
  user-level ``sshd`` inside each allocation to reach its nodes; nothing runs as
  root and no cluster-wide configuration is needed.
- ``pbsdsh`` on the compute nodes (part of every PBS installation).

In a typical workflow:

1. A cluster administrator sets up a PBS cluster and gives users SSH access to a
   login node.
2. Users describe their PBS cluster(s) in ``~/.pbs/config``. SkyPilot reads this
   file to communicate with the cluster(s).

Configuring PBS clusters
------------------------

SkyPilot uses an SSH config-style file at ``~/.pbs/config`` to connect to PBS
clusters. Each ``Host`` entry is one PBS cluster; its name is the SkyPilot
*region* (``--infra pbs/<cluster>``).

.. code-block:: bash

   $ mkdir -p ~/.pbs

   $ cat > ~/.pbs/config << EOF
   Host mycluster
       HostName login.mycluster.myorg.com
       User myusername
       IdentityFile ~/.ssh/id_rsa
       # Optional: Port 22
       # Optional: ProxyJump jumphost
       # Optional: ProxyCommand ssh -W %h:%p jumphost

   # Optional: more PBS clusters
   Host othercluster
       HostName login.other.myorg.com
       User myusername
   EOF

.. note::

    ``HostName`` and ``User`` are required. ``IdentityFile`` is optional; if it
    is not set, SSH uses keys from ssh-agent or the default key locations.

Password-only login nodes
~~~~~~~~~~~~~~~~~~~~~~~~~

Many HPC login nodes do not accept SSH keys. SkyPilot prompts for the password
(or one-time code) once per connection and keeps the connection alive. To avoid
prompts entirely, open a shared connection yourself and tell SkyPilot where its
control socket is:

.. code-block:: bash

   $ cat >> ~/.pbs/config << EOF
   Host mycluster
       HostName login.mycluster.myorg.com
       User myusername
       ControlMaster auto
       ControlPath ~/.ssh/pbs-%C
       ControlPersist 12h
   EOF

   # Open the connection once (enter your password), then launch as usual.
   $ ssh -F ~/.pbs/config -fN mycluster
   $ sky launch --infra pbs/mycluster -- hostname

SkyPilot reuses the ``ControlPath`` socket for every command it runs on the
login node, including the tunnel to the allocated nodes.

Verifying the setup
~~~~~~~~~~~~~~~~~~~

.. code-block:: console

   $ sky check pbs
   Checking credentials to enable infra for SkyPilot.
     PBS: enabled [compute]
       Allowed clusters:
       └── mycluster: enabled

``sky check`` also warns when the working directory is not on a shared
filesystem, or when no ``sshd`` binary is found.

Launching tasks
---------------

A PBS cluster is a region and its queues are zones:

.. code-block:: bash

   # Any PBS cluster and queue that fits
   $ sky launch --infra pbs --gpus A100:2 train.yaml

   # A specific cluster
   $ sky launch --infra pbs/mycluster --gpus A100:2 train.yaml

   # A specific cluster and queue
   $ sky launch --infra pbs/mycluster/gpuq --gpus A100:2 train.yaml

   # Multi-node
   $ sky launch --infra pbs/mycluster --num-nodes 4 --gpus A100:4 train.yaml

Without a queue, SkyPilot tries the cluster's default queue first and fails over
to the other queues (and to other infra) when the job is not allocated within
``provision_timeout``. With a queue, SkyPilot waits for the allocation.

Only queues that ``qsub`` accepts are offered: a routing queue counts (and is
usually the default queue on sites that route by resources), while execution
queues marked ``from_route_only`` are left out. Sites that tie nodes to queues
with a ``Qlist`` resource (``default_chunk.Qlist`` on the queue,
``resources_available.Qlist`` on the nodes) are understood when working out
which nodes a queue can use.

Under the hood, each SkyPilot cluster is **one PBS job** (``qsub``) that holds
its nodes for as long as the cluster lives: ``sky launch``, ``sky exec`` and
``sky jobs`` reuse that allocation, and ``sky down`` deletes the job
(``qdel``). Use ``sky status`` to see the cluster; the PBS job id is recorded in
the cluster's events (``sky status -v``).

.. code-block:: console

   $ sky launch -c dev --infra pbs/mycluster --gpus A100:1 -- nvidia-smi
   $ sky exec dev -- python train.py
   $ ssh dev          # lands in the cluster's working directory on the head node
   $ sky down dev

GPUs
~~~~

SkyPilot requests GPUs through the ``ngpus`` resource of the ``select`` chunk
(``-l select=N:ncpus=C:mem=M:ngpus=G``). If the nodes declare their GPU model
in a resource such as ``gpu_type`` (``pbsnodes`` shows it under
``resources_available``), SkyPilot resolves the requested name to the cluster's
spelling and adds ``:gpu_type=<model>`` to the chunk, so ``--gpus A100:1`` and
``--gpus L40S:1`` land on the right nodes. See :ref:`gpu_resource and
gpu_type_resource <config-yaml-pbs-gpu-resource>` to change the resource
names, and :ref:`gpu_queue_map <config-yaml-pbs-gpu-queue-map>` for clusters
that select GPU models by queue instead.

``sky show-gpus --infra pbs`` lists the GPUs of your PBS clusters, with the
free count per model.

Walltime, accounts and other qsub options
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Most sites require a walltime and an account or project. Set them once in
``~/.sky/config.yaml`` (globally, per cluster or per queue) or per task in the
task YAML's ``config`` block:

.. code-block:: yaml

   # ~/.sky/config.yaml
   pbs:
     qsub_options:
       walltime: "8:00:00"   # quoted: YAML reads 8:00:00 as a base-60 number
     quota:
       project: my-project   # qsub -P
     cluster_configs:
       mycluster:
         workdir: /scratch/$USER          # shared filesystem for cluster state
         select_resources:
           tmp_size: 400gb                # extra select resources
         queue_configs:
           gpuq:
             qsub_options:
               walltime: "24:00:00"

.. code-block:: yaml

   # task.yaml
   resources:
     infra: pbs/mycluster
     accelerators: A100:1
   config:
     pbs:
       qsub_options:
         walltime: "2:00:00"
       place: scatter:excl   # whole nodes

If no walltime is configured, SkyPilot requests the queue's
``resources_max.walltime`` when the queue has one, and otherwise submits
without a walltime so the queue's default applies. See
:ref:`the pbs config reference <config-yaml-pbs>` for every option.

How it works
------------

- ``sky launch`` writes a batch script to the cluster's working directory and
  submits it with ``qsub -N <cluster name> -q <queue>``. The script runs on the
  first allocated node, creates the cluster's working directory and keys, and
  starts a user-level ``sshd`` on a free port on every allocated node
  (through ``pbsdsh`` on all but the first).
- SkyPilot reaches each node over SSH through the login node
  (``ssh -W node:port``), using its own key. Every process it starts is a
  descendant of a PBS task, so it stays inside the job and is cleaned up with
  it. Sessions carry ``PBS_JOBID``, so sites whose ``/etc/ssh/sshrc`` attaches
  sessions to their job (``pbs_attach``) accept them.
- The SkyPilot runtime (its Python environment and job database) lives on
  node-local storage: the job's ``$TMPDIR`` when PBS sets one, else ``/tmp``
  (configurable with ``tmpdir``). ``~`` in tasks is the cluster's directory on
  the shared filesystem, with ``sky_workdir`` and ``sky_logs`` under it.
- Jobs on the same SkyPilot cluster (``sky exec``, ``sky jobs``) queue behind
  each other by CPUs and GPUs, as on other clouds; each job gets its own
  ``CUDA_VISIBLE_DEVICES``.

Limitations
-----------

- No ``sky stop``/``sky start``, autostop or autodown: the allocation ends with
  ``sky down`` or at its walltime. Use ``sky down`` to release the nodes.
- Docker images, opening ports and spot are not supported.
- Storage mounts (``MOUNT`` mode) need FUSE on the compute nodes; ``COPY`` mode
  always works.
- Job and serve controllers cannot run on PBS.

.. _slurm-getting-started:

Getting Started on Slurm
========================

.. note::

    **Early Access:** Slurm support is under active development. If you're interested in trying it out,
    please `fill out this form <https://forms.gle/rfdWQcd9oQgp41Hm8>`_.

Quickstart
----------
Have SSH access to a Slurm cluster? Get started with SkyPilot in 3 steps:

.. code-block:: bash

   # 1. Configure your Slurm cluster in ~/.slurm/config
   $ mkdir -p ~/.slurm && cat > ~/.slurm/config << EOF
   Host mycluster
       HostName login.mycluster1.myorg.com
       User myusername
       IdentityFile ~/.ssh/id_rsa
   EOF

   # 2. Verify SkyPilot detects your Slurm cluster
   $ sky check
   # Shows "Slurm: enabled"

   # 3. Launch your first SkyPilot task
   $ sky launch --gpus H100:1 -- nvidia-smi

For detailed instructions, prerequisites, and advanced features, read on.

Prerequisites
-------------

To connect and use a Slurm cluster, SkyPilot needs SSH access to the Slurm login node (where you can run ``sbatch``, ``squeue``, etc.).

In a typical workflow:

1. A cluster administrator sets up a Slurm cluster and provides users with SSH access to the login node.

2. Users configure the ``~/.slurm/config`` file with connection details for their Slurm cluster(s).
   SkyPilot reads this configuration file to communicate with the cluster(s).

Configuring Slurm clusters
--------------------------

SkyPilot uses an SSH config-style file at ``~/.slurm/config`` to connect to Slurm clusters.
Each host entry in this file represents a Slurm cluster.

Create the configuration file:

.. code-block:: bash

   $ mkdir -p ~/.slurm

   $ cat > ~/.slurm/config << EOF
   # Example Slurm cluster configuration
   Host mycluster1
       HostName login.mycluster1.myorg.com
       User myusername
       IdentityFile ~/.ssh/id_rsa
       # Optional: Port 22
       # Optional: ProxyCommand ssh -W %h:%p jumphost
    
   # Optional: Add more clusters if you have multiple Slurm clusters
   Host mycluster2
       HostName login.mycluster2.myorg.com
       User myusername
       IdentityFile ~/.ssh/id_rsa
   EOF

Verify your SSH connection works by running:

.. code-block:: bash

   $ ssh -F ~/.slurm/config <cluster_name> "sinfo"

Launching your first task
-------------------------
.. _slurm-instructions:

Once you have configured your Slurm cluster:

1. Run :code:`sky check` and verify that Slurm is enabled in SkyPilot.

   .. code-block:: console

     $ sky check

     Checking credentials to enable clouds for SkyPilot.
     ...
     Slurm: enabled
       Allowed clusters:
         ✔ mycluster1
         ✔ mycluster2
     ...


2. You can now run any SkyPilot task on your Slurm cluster.

   .. code-block:: console

        $ sky launch --cpus 2 task.yaml
        == Optimizer ==
        Target: minimizing cost
        Estimated cost: $0.0 / hour

        Considered resources (1 node):
        ---------------------------------------------------------------------------------------------------
         INFRA                   INSTANCE          vCPUs   Mem(GB)   GPUS     COST ($)   CHOSEN
        ---------------------------------------------------------------------------------------------------
         Slurm (mycluster1)      -                 2       4         -        0.00          ✔
         Slurm (mycluster2)      -                 2       4         -        0.00
         Kubernetes (myk8s)      -                 2       4         -        0.00
         AWS (us-east-1)         m6i.large         2       8         -        0.10
         GCP (us-central1-a)     n2-standard-2     2       8         -        0.10
        ---------------------------------------------------------------------------------------------------

   SkyPilot will submit a job to your Slurm cluster using ``sbatch``.

3. To run on a specific Slurm cluster, use the ``--infra`` flag:

   .. code-block:: bash

      $ sky launch --infra slurm/mycluster1 task.yaml


Viewing cluster status
----------------------

To view the status of your SkyPilot clusters on Slurm:

.. code-block:: console

    $ sky status
    NAME         LAUNCHED    RESOURCES                    STATUS   AUTOSTOP  COMMAND
    my-task      10 mins ago Slurm(mycluster1, 2CPU--4GB) UP       -         sky launch...

To terminate a cluster (cancels the underlying Slurm job):

.. code-block:: console

    $ sky down my-task


Using GPUs
----------

To request GPUs on your Slurm cluster, specify the accelerator in your task YAML:

.. code-block:: yaml

    # task.yaml
    resources:
      accelerators: H200:1

    run: |
      nvidia-smi

Or via the command line:

.. code-block:: bash

    $ sky launch --gpus H200:1 -- nvidia-smi

SkyPilot will translate this to the appropriate ``--gres=gpu:`` directive for Slurm.

.. note::

    The GPU type name should match what's configured in your Slurm cluster's GRES configuration.
    Common names include ``H100``, ``H200``, ``L4`` etc.


Shared filesystem (NFS)
-----------------------

Most Slurm clusters have a shared filesystem (typically NFS) that is mounted on all compute nodes.
SkyPilot leverages this existing setup - your home directory and files are automatically accessible
from your SkyPilot clusters and jobs.

This means your code, data, and outputs are available on all nodes without any additional configuration. 

If you have local files you want to use on the remote cluster, you can sync them from your local machine to the remote cluster using :ref:`file mounts and workdir <sync-code-artifacts>`.


Configuring allowed clusters
----------------------------

By default, SkyPilot will use all clusters defined in ``~/.slurm/config``.
To restrict which clusters SkyPilot can use, add the following to your ``~/.sky/config.yaml``:

.. code-block:: yaml

    slurm:
      allowed_clusters:
        - mycluster1
        - mycluster2


Current limitations
-------------------

Slurm support in SkyPilot is under active development. The following features are not yet supported:

* **Multinode jobs**: multinode jobs will be supported soon on Slurm.
* **Autostop**: Slurm clusters cannot be automatically terminated after idle time.
* **Custom images**: Docker or custom container images are not supported.
* **SkyServe**: Serving deployments on Slurm is not yet supported.

FAQs
----

* **How does SkyPilot interact with Slurm?**

  Each SkyPilot "cluster" corresponds to a Slurm job. When you run ``sky launch``, SkyPilot creates an sbatch script that requests the specified resources
  and runs a long-lived process with the SkyPilot runtime. 
  
  SkyPilot uses slurm CLI commands on the login node to interact with the cluster. It submits jobs using ``sbatch``, views the status of jobs using ``squeue``, and terminates jobs using ``scancel``.
  

* **Which user are jobs submitted as?**

  Jobs are submitted using your own Slurm username (the ``User`` specified in your ``~/.slurm/config``).
  This means your jobs appear under your username in ``squeue``, count against your quotas, and respect
  your existing permissions. 

* **Can I use multiple Slurm clusters?**

  Yes. Add multiple host entries to your ``~/.slurm/config`` file. Each host will appear as a separate
  region in SkyPilot's optimizer.

* **What partition does SkyPilot use?**

  We use the default partition for the Slurm cluster. Choosing a specific partition will be supported soon.

* **Can SkyPilot provision a Slurm cluster for me?**

  No. SkyPilot runs tasks on existing Slurm clusters. It does not provision new Slurm clusters
  or add nodes to existing clusters.

  


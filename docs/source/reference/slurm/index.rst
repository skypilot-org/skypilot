.. _slurm-overview:

Using Slurm
===========

.. note::

    **Early Access:** Slurm support is under active development. If you're interested in trying it out,
    please `fill out this form <https://forms.gle/rfdWQcd9oQgp41Hm8>`_.

SkyPilot tasks can be run on your Slurm clusters.
The Slurm cluster gets added to the list of "clouds" in SkyPilot and SkyPilot
tasks can be submitted to your Slurm cluster just like any other cloud provider.

Why use SkyPilot on Slurm?
--------------------------

.. tab-set::

    .. tab-item:: For AI Developers
        :sync: why-ai-devs-tab

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  🗂️ Multi-cluster made easy
                :text-align: center

                Access multiple Slurm clusters through one interface - no need to juggle different login nodes or sbatch scripts.

            .. grid-item-card::  🌍 Unified interface for all infra
                :text-align: center

                The same SkyPilot YAML works on Slurm, Kubernetes, and cloud VMs - switch between them seamlessly.

            .. grid-item-card::  🚀 Easy job submission
                :text-align: center

                No need to write complex sbatch scripts - write a simple SkyPilot YAML and run with one command ``sky launch``.

            .. grid-item-card::  ☁️ Burst to the cloud
                :text-align: center

                Slurm cluster is full? SkyPilot seamlessly gets resources on the cloud to get your job running sooner.


    .. tab-item:: For Slurm Admins
        :sync: why-admins-tab

        .. grid:: 2
            :gutter: 3

            .. grid-item-card::  🛠️ Manage multiple Slurm clusters
                :text-align: center

                Manage all your Slurm clusters from one interface - unified visibility and control across all your AI compute.

            .. grid-item-card::  🧩 Unified platform for all infra
                :text-align: center

                Let users scale beyond your Slurm cluster to capacity on :ref:`clouds and Kubernetes <auto-failover>` without manual intervention.

            .. grid-item-card::  🔗 Works with your existing Slurm setup
                :text-align: center

                SkyPilot works with your existing Slurm configuration - no changes needed to your cluster.

            .. grid-item-card::  🌍 Portable AI workloads
                :text-align: center

                Enable users to write workloads once and run them on Slurm, Kubernetes, or cloud VMs with the same interface.


Table of contents
-----------------

.. grid:: 1 1 2 2
    :gutter: 3

    .. grid-item-card:: 👋 Get Started
        :link: slurm-getting-started
        :link-type: ref
        :text-align: center

        Have SSH access to a Slurm login node? Launch your first SkyPilot task on Slurm - it's as simple as ``sky launch``.


.. toctree::
   :hidden:

   Getting Started <slurm-getting-started>
   Configuring Pricing <slurm-pricing>

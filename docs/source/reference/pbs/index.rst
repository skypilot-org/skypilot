.. _pbs-overview:

Using PBS Pro
=============

SkyPilot tasks can be run on your PBS Pro (or OpenPBS) clusters.
The PBS cluster gets added to the list of "clouds" in SkyPilot and SkyPilot
tasks can be submitted to it just like to any other cloud provider or to a
Slurm cluster.

Why use SkyPilot on PBS?
------------------------

.. grid:: 2
    :gutter: 3

    .. grid-item-card::  🗂️ Multi-cluster made easy
        :text-align: center

        Access several PBS clusters, Slurm clusters, Kubernetes clusters and clouds through one interface.

    .. grid-item-card::  🚀 Easy job submission
        :text-align: center

        No need to write ``qsub`` scripts - write a simple SkyPilot YAML and run it with ``sky launch``.

    .. grid-item-card::  🔗 Works with your existing PBS setup
        :text-align: center

        SkyPilot only needs SSH access to a login node; nothing is installed on the cluster.

    .. grid-item-card::  ☁️ Burst to the cloud
        :text-align: center

        PBS queue is full? SkyPilot can fail over to your other infra to get your job running sooner.

Table of contents
-----------------

.. grid:: 1 1 2 2
    :gutter: 3

    .. grid-item-card:: 👋 Get Started
        :link: pbs-getting-started
        :link-type: ref
        :text-align: center

        Have SSH access to a PBS login node? Launch your first SkyPilot task on PBS - it's as simple as ``sky launch``.


.. toctree::
   :hidden:

   Getting Started <pbs-getting-started>

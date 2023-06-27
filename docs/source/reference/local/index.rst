.. _local-overview:

SkyPilot On-prem
===================================

.. warning::
    SkyPilot On-prem is an experimental feature and is being redesigned. While it is still available for use, please be aware there may be rough edges. If you'd like to use SkyPilot On-prem, please reach out to us on `Slack <http://slack.skypilot.co>`_.

SkyPilot On-prem is a lightweight cluster manager and job scheduler for local clusters. SkyPilot On-prem enables multiple users to utilize SkyPilot's :ref:`job submission <job-queue>` feature to share resources on the same local cluster.

SkyPilot On-prem is readily deployable on top of any operating system and requires :code:`python3` to be globally installed on all machines.

Design
-------------------
SkyPilot cluster manager is central for running SkyPilot On-prem. It consists of the SkyPilot job scheduler, which resides on the head node and schedules jobs to be run on local nodes, and the SkyPilot job daemon, which resides on all local nodes and coordinates job logs and metadata with the head node.

SkyPilot has two types of users, **system administrators**, who have **sudo** access to the machine, and **regular users**, who submit jobs to the local cluster.

**System administrators** plays 4 important roles:

- Create and maintain user accounts on all nodes in the cluster.
- Install SkyPilot's dependencies on all nodes
- Submit a private cluster YAML file to SkyPilot, which launches its services on the local cluster
- Publish a distributable cluster YAML file for all regular users

**Regular users** receive the distributable cluster YAML file from the administrator and register the cluster into SkyPilot. No changes to SkyPilot's task YAML file are needed to launch jobs on the local cluster.

Table of Contents
-------------------
.. toctree::
   setup
   job-submission
   cluster-config

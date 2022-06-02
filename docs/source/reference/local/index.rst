.. _local-overview:
Sky On-prem
===================================

Sky On-prem is a lightweight cluster manager and job scheduler for local clusters. Sky On-prem enables multiple users to utilize Sky's :ref:`job submission <job-queue>` feature to share resources on the same local cluster.

Sky On-prem is readily deployable on top of any operating system and requires :code:`python3` to be globally installed on all machines.

Design
-------------------
Sky cluster manager is central for running Sky On-prem. It consists of the Sky job scheduler, which resides on the head node and schedules jobs to be run on local nodes, and the Sky job daemon, which resides on all local nodes and coordinates job logs and metadata with the head node.

Sky has two types of users, **system administrators**, who have **sudo** access to the machine, and **regular users**, who submit jobs to the local cluster.

**System administrators** plays 4 important roles:

- Create and maintain user accounts on all nodes in the cluster.
- Install Sky's dependencies on all nodes
- Submit a private cluster YAML file to Sky, which launches its services on the local cluster
- Publish a distributable cluster YAML file for all regular users

**Regular users** receive the distributable cluster YAML file from the administrator and register the cluster into Sky. No changes to Sky's task YAML file are needed to launch jobs on the local cluster.

Table of Contents
-------------------
.. toctree::
   setup
   job-submission
   cluster-config


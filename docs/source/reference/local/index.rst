.. _local-overview:
Sky on Prem
===================================

Sky on Prem is a lightweight cluster manager and job scheduler for local clusters. Sky on Prem enables multiple users to utilize Sky's :ref:`job submission <job-queue>` feature to share resources on the same local cluster.

Sky on Prem is readily deployable on top of any operating system and requires :code:`python3` to be globally installed on all machines.

Design
-------------------
Central to Sky on Prem is the Sky cluster manager. The cluster manager consists of the Sky job scheduler, which resides on the head node and schedules jobs to be run on local nodes, and the Sky job daemon, which resides on all local nodes and coordinates job logs and metadata with the head node.

Sky has two types of users, the **system administrator**, who has **sudo** access to the machine, and **regular users**, who submit jobs to the local cluster.

The **system administrator** plays 3 important roles:

- Installs Sky's dependency on all nodes
- Submits a private cluster YAML file to Sky, which launches its services on the local cluster
- Publishes a distributable cluster YAML file for all users

**Regular users** receive the distributable cluster YAML file from the administrator and register the cluster into Sky. Minimal changes to Sky's task YAML file are needed to launch jobs on the local cluster.

Table of Contents
-------------------
.. toctree::
   setup
   job-submission
   cluster-config


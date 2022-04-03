.. _iter-dev:
Iteratively Developing a Project
====================================

This page shows a typical workflow for iteratively developing and running a
project on Sky.

Getting an interactive node
------------------
:ref:`Interactive nodes <interactive-nodes>` are easy-to-spin-up VMs that enable **fast development and interactive debugging**.

To provision a GPU interactive node named :code:`dev`, run

.. code-block:: console

  $ # Provisions/reuses an interactive node with a single K80 GPU.
  $ sky gpunode -c dev --gpus K80

See the :ref:`CLI reference <sky-gpunode>` for all flags such as changing the GPU type and count.

Running code
--------------------
To run a command or a script on the cluster, use :code:`sky exec`:

.. code-block:: console

  $ # If the user has written a task.yaml, this directly
  $ # executes the `run` section in the task YAML:
  $ sky exec dev task.yaml

  $ # Run a script inside the workdir.
  $ # Workdir contents are synced to the cluster (~/sky_workdir/).
  $ sky exec dev -- python train.py

  $ # Run a command.
  $ sky exec dev -- gpustat -i

Alternatively, the user can directly :code:`ssh` into the cluster's nodes and run commands:

.. code-block:: console

  $ # SSH into head node
  $ ssh dev

  $ # SSH into worker nodes
  $ ssh dev-worker1
  $ ssh dev-worker2

Sky provides easy password-less SSH access by automatically creating entries for each cluster in :code:`~/.ssh/config`.
Referring to clusters by names also allows for seamless integration with common tools
such as :code:`scp`, :code:`rsync`, and `Visual Studio Code Remote
<https://code.visualstudio.com/docs/remote/remote-overview>`_.

.. note::

  Refer to :ref:`Syncing Code and Artifacts` for more details
  on how to upload code and download outputs from the cluster.

Ending a development session
-----------------------------
To end a development session:

.. code-block:: console

  $ # Stop at the end of the work day:
  $ sky stop dev

  $ # Or, to terminate:
  $ sky down dev

To restart a stopped cluster:

.. code-block:: console

  $ # Restart it the next morning:
  $ sky start dev

.. note::

    Stopping a cluster does not lose data on the attached disks (billing for the
    instances will stop while the disks will still be charged).  Those disks
    will be reattached when restarting the cluster.

    Terminating a cluster will delete all associated resources (all billing
    stops), and any data on the attached disks will be lost.  Terminated
    clusters cannot be restarted.

.. _iter-dev:
Iteratively Developing a Project
====================================

This page walks through a typical workflow for iteratively developing a machine
learning project on Sky.

Provisioning a VM
------------------
To provision a GPU-based :ref:`interactive node <interactive-nodes>` named :code:`dev`, run

.. code-block:: console

  $ # Provisions/reuses an interactive node with a single K80 GPU.
  $ sky gpunode -c dev --gpus K80

Interactive nodes are easy-to-spin-up VMs that allow for fast development and interactive debugging.
See the :ref:`CLI reference <sky-gpunode>` for all configuration options.

Running code
--------------------
To run a command or a script on the cluster, use :code:`sky exec`:

.. code-block:: bash

  # Run a bash command without logging in.
  $ sky exec dev -- gpustat -i

  # Run a python script.
  # train.py must live in the remote workdir (~/sky_workdir).
  $ sky exec dev -- python train.py

  # If the user has written a task.yaml, this directly
  # executes the `run` section in the task YAML:
  $ sky exec dev task.yaml

Alternatively, the user can directly :code:`ssh` into the head node of the cluster and run commands:

.. code-block:: console

  $ ssh dev

Sky provides easy password-less SSH access by automatically creating an entry for each cluster in the :code:`~/.ssh/config` file.
Referring to clusters by names also allows for seamless integration with common tools
such as :code:`scp`, :code:`rsync`, and `Visual Studio Code Remote
<https://code.visualstudio.com/docs/remote/remote-overview>`_.

.. note::

  Refer to :ref:`syncing code and artifacts <sync-code-artifacts>` for more details
  on how to upload code and download outputs from the cluster.

Ending a development session
-----------------------------
To end a development session, run the following commands:

.. code-block:: console

  $ sky stop dev

  # Or, to terminate:
  $ sky down dev

To restart a stopped cluster:

.. code-block:: console

  $ sky start dev

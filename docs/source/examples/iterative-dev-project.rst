.. _iter-dev:
Iteratively Developing a Project
====================================

This page walks through a typical workflow for iteratively developing a machine
learning project on Sky.

Provisioning a VM
------------------
To provision a virtual machine named :code:`dev`, run

.. code-block:: console

  $ sky gpunode -c dev

By default, :ref:`GPU nodes <interactive-nodes>` are provisioned on the cheapest cloud with a single K80 GPU.
To use other GPUs, see the :ref:`CLI reference <cli>` for all configuration options.

Development
------------
To log in to the machine, Sky provides easy password-less SSH access:

.. code-block:: console

  $ ssh dev

Referring to clusters by names also allows for integration with common tools
such as :code:`scp`, :code:`rsync`, and `Visual Studio Code Remote
<https://code.visualstudio.com/docs/remote/remote-overview>`_.

Running code
--------------------
To run a project on the cluster without logging in, use :code:`sky exec`:

.. code-block:: bash

  # Run a bash command without logging in.
  $ sky exec dev -- python train.py
  $ sky exec dev -- gpustat -i

  # If the user has written a task.yaml, this directly executes the
  # `run` section defined in the task specification:
  $ sky exec dev task.yaml

Alternatively, the user can also run commands after logging into the VM via :code:`ssh`.

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

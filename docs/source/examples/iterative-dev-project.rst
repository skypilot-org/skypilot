.. _iter-dev:
Iteratively Developing a Project
====================================

In this example we will do a walkthrough of a typical workflow for iteratively
developing a machine learning project on Sky.

Provisioning a VM
------------------
To provision a virtual machine named dev, run the following command:

.. code-block:: console

  $ sky gpunode -c dev

By default, GPU nodes are provisioned on the cheapest cloud with a single K80 GPU.
If you prefer, you can review our CLI reference for all configuration options.

Development
------------
To log in to the machine, Sky provides easy password-less SSH access:

.. code-block:: console

  $ ssh dev

This also allows for integration with common tools such as :code:`scp`, :code:`rsync`, and
VSCode Remote.

Running your code
--------------------
You can run your project by directly logging into the VM and running shell commands, but Sky also
provides remote execution without logging in:

.. code-block:: bash

  # Run a bash command without logging in.
  $ sky exec dev -- python train.py
  $ sky exec dev -- gpustat -i

  # If you have a task.yaml, you can also directly execute the `run` section
  # defined in the task specification.
  $ sky exec dev task.yaml

Syncing local code to cluster
--------------------------------------
Sky **simplifies code syncing** by the automatically syncing a local working
directory to the cluster. Syncing happens on every :code:`sky launch` and
:code:`sky exec`, so you can edit code locally and transparently upload them to
the a cluster.

The working directory can be configured either (1) with the :code:`workdir`
field in a :ref:`task YAML file <yaml-spec>`, or (2) using the command line
option :code:`--workdir`:

.. code-block::

  $ sky launch --workdir=/path/to/code task.yaml
  $ sky exec --workdir=/path/to/code task.yaml

These commands sync the working directory to :code:`~/sky_workdir` on the remote
VMs, and the task is invoked under that working directory (so that it can invoke
scripts, access checkpoints, etc.).

Transferring artifacts
--------------------------------------
Use the familiar scp/rsync to transfer files between your local machine and the
head node of a cluster:

.. code-block::

  $ rsync -Pavz my_code/ dev:/path/to/destination  # copy files to head node
  $ rsync -Pavz dev:/path/to/source my_code/       # copy files from head node

.. note::
    Sky currently does not natively support transfering artifacts from/to
    **worker machines** of a multi-node cluster.  As temporary workarounds,
    query the worker IPs from the cloud console, and run :code:`rsync -Pavz -e
    'ssh -i ~/.ssh/sky-key' <worker_ip>:/path /local_path`.

Ending a development session
-----------------------------
To end a development session, run the following command:

.. code-block:: console

  $ sky stop dev

  # Or, to terminate:
  $ sky down dev

To restart a stopped cluster:

.. code-block:: console

  $ sky start dev

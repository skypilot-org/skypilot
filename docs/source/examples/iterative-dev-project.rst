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

Syncing code and transferring artifacts
--------------------------------------
Use the familiar scp/rsync to transfer files between your local machine and remote VM:

.. code-block::

  $ scp -r my_code/ dev:/path/to/destination  # copy files to remote VM
  $ scp -r dev:/path/to/source my_code/       # copy files from remote VM

Sky **simplifies code syncing** by the automatic transfer of a working directory
to the cluster.  The working directory can be configured with the
:code:`workdir` option in a task YAML file, or using the following command line
option:

.. code-block::

  $ sky launch --workdir=/path/to/code task.yaml
  $ sky exec --workdir=/path/to/code task.yaml

These commands sync the working directory to a location on the remote VM, and
the task is run under that working directory (e.g., to invoke scripts, access
checkpoints, etc.).

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

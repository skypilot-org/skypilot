.. _iter-dev:
Iteratively Developing a Project
====================================

In this example we will do a walkthrough of a typical workflow for iteratively
developing a machine learning project on Sky.

Provisioning a VM
------------------
To provision a virtual machine named :code:`dev`, run

.. code-block:: console

  $ sky gpunode -c dev

By default, GPU nodes are provisioned on the cheapest cloud with a single K80 GPU.
To use other GPUs, see the :ref:`CLI reference <cli>` for all configuration options.

Development
------------
To log in to the machine, Sky provides easy password-less SSH access:

.. code-block:: console

  $ ssh dev

This also allows for integration with common tools such as :code:`scp`, :code:`rsync`, and
`Visual Studio Code Remote <https://code.visualstudio.com/docs/remote/remote-overview>`_.

Running your code
--------------------
Run your project on the cluster without logging in, using :code:`sky exec`:

.. code-block:: bash

  # Run a bash command without logging in.
  $ sky exec dev -- python train.py
  $ sky exec dev -- gpustat -i

  # If you have a task.yaml, you can also directly execute the `run` section
  # defined in the task specification.
  $ sky exec dev task.yaml

You can also run your project by directly logging into the VM and running commands.

Syncing local code to cluster
--------------------------------------
Sky **simplifies code syncing** by automatically syncing a local working
directory to a cluster. Syncing happens on every :code:`sky launch` and
:code:`sky exec`, so you can edit code locally and transparently have them
uploaded to remote clusters.

The working directory can be configured either (1) with the :code:`workdir`
field in a :ref:`task YAML file <yaml-spec>`, or (2) using the command line
option :code:`--workdir`:

.. code-block:: bash

  $ sky launch -c dev --workdir=/path/to/code task.yaml
  $ sky exec dev --workdir=/path/to/code task.yaml

These commands sync the working directory to :code:`~/sky_workdir` on the remote
VMs, and the task is invoked under that working directory (so that it can invoke
scripts, access checkpoints, etc.). Sky ignores files and directories during upload
the same way git does: any items that are included in a :code:`.gitignore` contained in the
working directory tree are not uploaded.

Transferring artifacts
--------------------------------------
Use the familiar scp/rsync to transfer files between your local machine and the
head node of a cluster:

.. code-block:: bash

  $ rsync -Pavz my_code/ dev:/path/to/destination  # copy files to head node
  $ rsync -Pavz dev:/path/to/source my_code/       # copy files from head node

.. note::
    Sky currently does not natively support **downloading artifacts from the
    worker machines** of a multi-node cluster.  As temporary workarounds, query
    the worker IPs from the cloud console, and run :code:`rsync -Pavz -e 'ssh -i
    ~/.ssh/sky-key' <worker_ip>:/path /local_path`. **Uploading files to a
    multi-node cluster**, both head and workers, is supported via
    :ref:`file_mounts <yaml-spec>`.

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

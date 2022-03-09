.. _sync-code-artifacts:
Syncing Code and Artifacts
====================================

Sky simplifies transferring code (the working directory) and artifacts to and
from cloud clusters.

Syncing local code to clusters
--------------------------------------
Sky **automatically syncs a local working directory to a cluster** on every
:code:`sky launch` and :code:`sky exec`.  This means you can keep and edit code
in one central place (the local machine where :code:`sky` is used) and have them
transparently uploaded to remote clusters for execution.

The working directory can be configured either (1) with the :code:`workdir`
field in a :ref:`task YAML file <yaml-spec>`, or (2) by using the command line
option :code:`--workdir`:

.. code-block:: console

  $ sky launch -c dev --workdir=/path/to/code task.yaml
  $ sky exec dev --workdir=/path/to/code task.yaml

These commands sync the working directory to :code:`~/sky_workdir` on the remote
VMs, and the task is invoked under that working directory (so that it can invoke
scripts, access checkpoints, etc.).

.. note::

  Keeping code in sync across multiple clusters is simplified:

  .. code-block:: console

    $ sky exec cluster0 task.yaml

    $ # Make local edits to the workdir...
    $ # cluster1 will get the updated code.
    $ sky exec cluster1 task.yaml

.. note::

  Sky ignores files and directories during upload the same way git does: any
  items that are included in a :code:`.gitignore` contained in the working
  directory tree are not uploaded.

Transferring artifacts
--------------------------------------
After a task's execution, artifacts such as **logs and checkpoints** may be
transferred from remote clusters to the local machine.

To transfer files from and to the head node of a cluster, use :code:`rsync` (or :code:`scp`):

.. code-block:: console

  $ rsync -Pavz dev:/path/to/checkpoints local/
  $ rsync -Pavz local/ dev:/path/to/checkpoints

.. note::
    For a multi-node cluster, Sky currently does not natively support
    downloading artifacts from the worker machines.  As temporary workarounds,
    query the worker IPs from the cloud console, and run :code:`rsync -Pavz -e
    'ssh -i ~/.ssh/sky-key' <worker_ip>:/path /local_path`.

    Uploading files to a
    multi-node cluster, both head and workers, is supported via
    :ref:`file_mounts <yaml-spec>`.

.. _sync-code-artifacts:
Syncing Code and Artifacts
====================================

Sky simplifies transferring code, data, and artifacts to and
from cloud clusters:

- To :ref:`upload code and project files<Uploading code and project files>` - use :code:`workdir`

- To :ref:`upload files outside of workdir<Uploading files outside of workdir>` (e.g., dotfiles) - use :code:`file_mounts`

- To :ref:`upload/reuse large files<Uploading or reusing large files>` (e.g., datasets) - use :ref:`Sky Storage <sky-storage>`

- To :ref:`download files and artifacts from a cluster<Downloading files and artifacts>` - use :code:`rsync`

Here, "upload" means uploading files from your local machine (or a cloud object
store) to a Sky cluster, while "download" means the reverse direction.  The same
mechanisms work for both files and directories.

Uploading code and project files
--------------------------------------
Sky **automatically syncs a local working directory to a cluster** on every
:code:`sky launch` and :code:`sky exec`.  The workdir contains a project's
code and other files, and is typically a Git folder.

The working directory can be configured either

(1) by the :code:`workdir` field in a :ref:`task YAML file <yaml-spec>`, or
(2) by the command line option :code:`--workdir`:

.. code-block:: console

  $ # Assuming task.yaml has a 'workdir: <path>' field, these commands
  $ # sync the workdir to the cluster:
  $ sky launch -c dev task.yaml
  $ sky exec dev task.yaml

  $ # Add a --workdir flag if the yaml doesn't contain the field, or
  $ # to override it.

These commands sync the working directory to :code:`~/sky_workdir` on the remote
VMs.  The task is invoked under that working directory (so that it can call
scripts, access checkpoints, etc.).

.. note::

    For large, multi-gigabyte workdirs (e.g., large datasets/checkpoints in the working
    directory), uploading may be slow because the they are synced to the remote VM(s)
    with :code:`rsync`. To exclude large files in your workdir from being uploaded,
    add them to the :code:`.gitignore` file under the workdir.

.. note::

  You can keep and edit code in one central place---the local machine where
  :code:`sky` is used---and have them transparently synced to multiple remote
  clusters for execution:

  .. code-block:: console

    $ sky exec cluster0 task.yaml

    $ # Make local edits to the workdir...
    $ # cluster1 will get the updated code.
    $ sky exec cluster1 task.yaml


Uploading files outside of workdir
--------------------------------------

Use the :code:`file_mounts` field in a :ref:`task YAML <yaml-spec>` to upload to a cluster

- local files outside of the working directory (e.g., dotfiles)
- cloud object store URIs (currently, Sky supports AWS S3 and GCP GCS)

Every :code:`sky launch` invocation reruns the sync up of these files.

Example file mounts:

.. code-block:: yaml

  file_mounts:
    # Format: <cluster path>: <local path/cloud object URI>

    # Upload from local machine to the cluster via rsync.
    /remote/datasets: ~/local/datasets
    ~/.vimrc: ~/.vimrc
    ~/.ssh/id_rsa.pub: ~/.ssh/id_rsa.pub

    # Download from S3 to the cluster.
    /s3-data-test: s3://fah-public-data-covid19-cryptic-pockets/human/il6/PROJ14534/RUN999/CLONE0/results0


For more details, see `this example <https://github.com/sky-proj/sky/blob/master/examples/using_file_mounts.yaml>`_ and :ref:`YAML Configuration <yaml-spec>`.


Uploading or reusing large files
--------------------------------------

For large files (e.g., 10s or 100s of GBs), putting them into the workdir or a
file_mount may be too slow, because they are processed by ``rsync``.  Use
:ref:`Sky Storage <sky-storage>` (cloud object stores) to efficiently handling
large files.


Downloading files and artifacts
--------------------------------------
After a task's execution, artifacts such as **logs and checkpoints** may be
transferred from remote clusters to the local machine.

To transfer files from the head node of a cluster, use :code:`rsync` (or :code:`scp`):

.. code-block:: console

  $ rsync -Pavz dev:/path/to/checkpoints local/

.. note::
    For a multi-node cluster, Sky currently does not natively support
    downloading artifacts from the worker machines.  As temporary workarounds,
    query the worker IPs from the cloud console, and run :code:`rsync -Pavz -e
    'ssh -i ~/.ssh/sky-key' <worker_ip>:/path /local_path`.

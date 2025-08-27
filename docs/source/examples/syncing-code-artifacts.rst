.. _sync-code-artifacts:

Syncing Code, Git, and Files
====================================

SkyPilot simplifies transferring code, data, and artifacts to and
from cloud clusters:

- To :ref:`sync code from a local directory or a git repository <sync-code-and-project-files-git>`: use :code:`workdir`

- To :ref:`upload files outside of workdir <file-mounts-example>` (e.g., dotfiles): use :code:`file_mounts`

- To :ref:`upload/reuse large files <uploading-or-reusing-large-files>` (e.g., datasets): use :ref:`SkyPilot bucket mounting <sky-storage>`

- To :ref:`download files and artifacts from a cluster <downloading-files-and-artifacts>`: use :ref:`SkyPilot bucket mounting <sky-storage>` or :code:`rsync`

Here, "upload" means uploading files from your local machine (or a cloud object
storage) to a SkyPilot cluster, while "download" means the reverse direction.  The same
mechanisms work for both files and directories.

.. _sync-code-and-project-files-git:

Sync code from a local directory or a git repository
--------------------------------------------------------------------

Use ``workdir`` to **automatically sync a local working directory or a Git repo
URL** to a remote cluster on every ``sky launch`` and ``sky exec``.

``workdir`` is synced/cloned to ``~/sky_workdir`` on the remote cluster. Commands in ``setup`` and
``run`` will be executed under ``~/sky_workdir``.

.. tab-set::

    .. tab-item:: Local Directory
        :sync: local-directory-tab

        If ``workdir`` is a local path, the entire directory is synced to the remote cluster. To exclude files from syncing, see :ref:`exclude-uploading-files`.

        If a relative path is used, it's evaluated relative to the location from which ``sky`` is called.

        The working directory can be configured either:

        1. By the :code:`workdir` field in a :ref:`task YAML file <yaml-spec-workdir>`

        .. code-block:: yaml

          # task.yaml
          workdir: ~/my-code

        2. By the CLI option :code:`--workdir`

        .. code-block:: console

          $ sky launch -c dev --workdir ~/my-code task.yaml
          $ sky exec dev --workdir ~/my-code task.yaml

        .. note::
          To exclude large files from being uploaded, see :ref:`exclude-uploading-files`.

    .. tab-item:: Git Repository
        :sync: git-repository-tab

        To set ``workdir`` to a git repository, specify the ``workdir.url``
        field (repo URL) and the ``workdir.ref`` field (git reference to
        checkout).

        The working directory can be configured either:

        1. By the :code:`workdir` field in a :ref:`task YAML file <yaml-spec-workdir>`

        .. code-block:: yaml

          # task.yaml
          workdir:
            url: https://github.com/skypilot-org/skypilot.git
            ref: main

        2. By the CLI options :code:`--git-url` and :code:`--git-ref`

        .. code-block:: console

          $ sky launch -c dev --git-url https://github.com/skypilot-org/skypilot.git --git-ref main task.yaml
          $ sky exec dev --git-url https://github.com/skypilot-org/skypilot.git --git-ref main task.yaml

        You can use different git references for different ``exec`` or ``launch`` commands to run the tasks with different code:

        .. code-block:: console

          $ sky exec cluster0 --git-ref <commit-1> task.yaml
          $ sky exec cluster0 --git-ref <commit-2> task.yaml

        Refer to :ref:`the reference <yaml-spec-workdir>` for more details.

        .. note::

          **Authentication for Private Repositories**

          **For HTTPS URLs**: Set the ``GIT_TOKEN`` environment variable. SkyPilot will automatically use this token for authentication.

          **For SSH/SCP URLs**: SkyPilot will attempt to authenticate using SSH keys in the following order:

          1. SSH key specified by the ``GIT_SSH_KEY_PATH`` environment variable
          2. SSH key configured in ``~/.ssh/config`` for the git host
          3. Default SSH key at ``~/.ssh/id_rsa``
          4. Default SSH key at ``~/.ssh/id_ed25519`` (if ``~/.ssh/id_rsa`` does not exist)

.. _file-mounts-example:

Uploading files outside of workdir
--------------------------------------

Use the :code:`file_mounts` field in a :ref:`task YAML <yaml-spec>` to upload to a cluster

- local files outside of the working directory (e.g., dotfiles)
- cloud object storage URIs (currently, SkyPilot supports AWS S3, GCP GCS, Cloudflare R2 and IBM COS)

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


For more details, see `this example <https://github.com/skypilot-org/skypilot/blob/master/examples/using_file_mounts.yaml>`_ and :ref:`YAML Configuration <yaml-spec>`.

If you have edited the ``file_mounts`` section and would like to have it reflected on an existing cluster without rerunning the ``setup`` commands,
pass the ``--no-setup`` flag to ``sky launch``. For example, ``sky launch --no-setup -c <cluster_name> <task.yaml>``.

.. note::

    Items listed in a :code:`.skyignore` file under the local file_mount source
    are also ignored (the same behavior as handling ``workdir``).

.. note::

    If relative paths are used in :code:`file_mounts` or :code:`workdir`, they
    are evaluated relative to the location from which the :code:`sky` command
    is run.

.. _uploading-or-reusing-large-files:

Uploading or reusing large files
--------------------------------------

For large files (e.g., 10s or 100s of GBs), putting them into the workdir or a
file_mount may be slow because they are processed by ``rsync``.  Use
:ref:`SkyPilot bucket mounting <sky-storage>` to efficiently handle
large files.

.. _exclude-uploading-files:

Exclude uploading files
--------------------------------------
By default, SkyPilot uses your existing :code:`.gitignore` and :code:`.git/info/exclude` to exclude files from syncing.

Alternatively, you can use :code:`.skyignore` if you want to separate SkyPilot's syncing behavior from Git's.
If you use a :code:`.skyignore` file, SkyPilot will only exclude files based on that file without using the default Git files.

Any :code:`.skyignore` file under either your workdir or source paths of file_mounts is respected.

:code:`.skyignore` follows RSYNC filter rules, e.g.

.. code-block::

  # Files that match pattern under CURRENT directory
  /file.txt
  /dir
  /*.jar
  /dir/*.jar

  # Files that match pattern under ALL directories
  *.jar
  file.txt

Do _not_ use ``.`` to indicate local directory (e.g., instead of ``./file``, write ``/file``).

.. _downloading-files-and-artifacts:

Downloading files and artifacts
--------------------------------------

Task artifacts, such as **logs and checkpoints**, can either be
transparently uploaded to a cloud object storage, or directly copied from the
remote cluster.

Writing artifacts to cloud object storage
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In addition to handling datasets and other task inputs,
:ref:`SkyPilot bucket mounting <sky-storage>` can also be used to directly upload artifacts
generated by tasks. This is achieved by creating a :code:`MOUNT` mode Storage
mount like so in your task YAML:

.. code-block:: yaml

    file_mounts:
      /outputs:
        name: my-sky-outputs    # Can be existing S3 bucket or a new bucket
        store: s3
        mode: MOUNT

This :code:`file_mount` will mount the bucket :code:`s3://my-sky-outputs/`
(creating it if it doesn't exist) at :code:`/outputs`. Since this is specified
with :code:`mode: MOUNT`, any files written to :code:`/outputs` will also be
automatically written to the :code:`s3://my-sky-outputs/` bucket.

Thus, if you point your code to produce files at :code:`/outputs/`, they
will be available on the S3 bucket when they are written to :code:`/outputs/`.
You can then fetch those files either using the `S3 web console <https://s3.console.aws.amazon.com/s3/buckets>`_ or aws-cli
(e.g., :code:`aws s3 ls my-sky-outputs`).


Transferring directly with rsync
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Files and artifacts can also be directly transferred from remote clusters to the
local machine.

To transfer files from cluster nodes, use :code:`rsync` (or :code:`scp`):

.. code-block:: console

  $ # Rsync from head
  $ rsync -Pavz dev:/path/to/checkpoints local/

  $ # Rsync from worker nodes (1-based indexing)
  $ rsync -Pavz dev-worker1:/path/to/checkpoints local/

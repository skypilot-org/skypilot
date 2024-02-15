.. _sky-storage:

Accessing Object Stores
=======================

SkyPilot tasks can access objects in cloud object stores such as AWS S3, Google Cloud Storage (GCS), Cloudflare R2 or IBM COS.

These objects are made available to the task at a local path on the remote VM, so
the task can access these objects as if they were local files.

Object stores to be used are specified using the :code:`file_mounts` field in a SkyPilot task.

Common Use Cases
----------------

.. tab-set::

    .. tab-item:: Use existing bucket
        :sync: existing-bucket-tab

        To access a bucket created externally (e.g., through cloud CLI or other tools),
        specify ``source``.

        .. code-block:: yaml

          # Mount an existing S3 bucket
          file_mounts:
            /mydata:
                source: s3://my-bucket/ # or gs://, r2://, cos://<region>/<bucket>
                mode: MOUNT  # Optional - either MOUNT or COPY. Default to MOUNT.

        This will `mount <storage-mounting-modes_>`__ the contents of the bucket at ``s3://my-bucket/`` to the remote VM at ``/mydata``.


    .. tab-item:: Upload files to new bucket
        :sync: new-bucket-tab

        To create a new bucket, upload local files to this bucket and attach it to the task,
        specify ``name`` and ``source``, where ``source`` is a local path.

        .. code-block:: yaml

          # Create a new S3 bucket and upload local data
          file_mounts:
            /mydata:
                name: my-sky-bucket
                source: ~/dataset   # Optional - path to local data to upload to the bucket
                store: s3   # Optional - either of s3, gcs, r2, ibm

        SkyPilot will create a S3 bucket called ``my-sky-bucket`` and upload the
        contents of ``~/dataset`` to it. The bucket will then be mounted at ``/mydata``
        and your data will be available to the task.

        If the bucket already exists and was created by SkyPilot, SkyPilot will fetch
        and re-use the bucket.

        If ``store`` is omitted, SkyPilot will use the same cloud provider as the task's cloud.

    .. tab-item:: Create empty bucket
        :sync: empty-bucket-tab

        To create an empty bucket, specify only the ``name`` and omit the ``source``.

        .. code-block:: yaml

          # Create an empty gcs bucket
          file_mounts:
            /mydata:
                name: my-sky-bucket
                store: gcs   # Optional - either of s3, gcs, r2, ibm

        SkyPilot will create an empty GCS bucket called ``my-sky-bucket`` and mount it at ``/mydata``.
        This empty bucket can be used to write checkpoints, logs or other outputs directly to the cloud.

        Since writes are replicated in `mount mode <storage-mounting-modes_>`__ (set by default),
        it can also act as a shared file system across workers running on different nodes.
        This is useful for `inter-process communication (IPC) <https://github.com/skypilot-org/skypilot/blob/master/examples/storage/pingpong.yaml>`_
        or for sharing files between workers.

You can find more detailed usage examples in `storage_demo.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/storage_demo.yaml>`_.

.. _storage-mounting-modes:

Mounting Modes
--------------

A cloud storage can used in either :code:`MOUNT` mode or :code:`COPY` mode.

1. **MOUNT** mode: The object store is directly "mounted" to the remote VM. I.e., files are streamed when accessed by the task and all writes are replicated to remote bucket (and any other VMs mounting the same bucket). This is the default mode.
2. **COPY** mode: The files are pre-fetched and cached on the local disk. Writing to object stores is not supported in this mode.

.. image:: ../images/sky-storage-modes.png
    :width: 800
    :align: center
    :alt: sky-storage-modes



Considerations for picking a mounting mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

TODO - make a table.

Performance
*  Compared to file_mounts, storage is faster and can persist across runs, requiring fewer uploads from your local machine.

Writes
* Certain ops will fail

Size


.. note::
    sky.Storage does not guarantee preservation of file
    permissions - you may need to set file permissions during task execution.

.. note::
    Symbolic links are handled differently in :code:`file_mounts` depending on whether SkyPilot Storage is used.
    For mounts backed by SkyPilot Storage, symbolic links are not copied to remote.
    For mounts not using SkyPilot Storage (e.g., those using rsync) the symbolic links are directly copied, not their target data.
    The targets must be separately mounted or else the symlinks may break.

.. note::
    :code:`MOUNT` mode employs a close-to-open consistency model. This means calling
    :code:`close()` on a file will upload the entire file to the backing object store.
    Any subsequent reads, either using SkyPilot Storage or external utilities (such as
    aws/gsutil cli) will see the latest data.

.. note::
    :code:`MOUNT` mode does not support the full POSIX interface and some file
    operations may fail. Most notably, random writes and append operations are
    not supported.

.. note::
    Storage only supports uploading directories (i.e., :code:`source` cannot be a file).
    To upload a single file to a bucket, please put in a directory and specify the directory as the source.
    To directly copy a file to a VM, please use regular :ref:`file mounts <file-mounts-example>`.


Using SkyPilot Storage CLI
--------------------------------

To manage object stores created by SkyPilot, the sky CLI provides two useful commands -
:code:`sky storage ls` and :code:`sky storage delete`.

1.  :code:`sky storage ls` shows the currently provisioned Storage objects.

.. code-block:: console

    $ sky storage ls
    NAME               CREATED     STORE  COMMAND                                        STATUS
    sky-dataset        3 mins ago  S3     sky launch -c demo examples/storage_demo.yaml  READY

2.  :code:`sky storage delete` allows you to delete any Storage objects managed
    by sky.

.. code-block:: console

    $ sky storage delete sky-dataset
    Deleting storage object sky-dataset...
    I 04-02 19:42:24 storage.py:336] Detected existing storage object, loading Storage: sky-dataset
    I 04-02 19:42:26 storage.py:683] Deleting S3 Bucket sky-dataset

.. note::
    :code:`sky storage ls` only shows Storage objects whose buckets were created
    by sky. Storage objects using externally created buckets or public buckets
    are not listed in :code:`sky storage ls` and cannot be managed through SkyPilot.

Storage YAML reference
----------------------

::

    sky.Storage

    Fields:
      sky.Storage.name: str
        Identifier for the storage object.

      sky.Storage.source: str
        The source attribute specifies the local path that must be made available
        in the storage object. It can either be a local path or a list of local
        paths or it can be a remote path (s3://, gs://, r2://, cos://<region_name>).
        If the source is local, data is uploaded to the cloud to an appropriate
        object store (s3, gcs, r2, or ibm). If the path is remote, the data is copied
        or mounted directly (see mode flag below).

      sky.Storage.store: str; either of 's3', 'gcs', 'r2', 'ibm'
        If you wish to force sky.Storage to be backed by a specific cloud object
        store, you can specify it here. If not specified, SkyPilot chooses the
        appropriate object store based on the source path and task's cloud provider.

      sky.Storage.persistent: bool
        Whether the remote backing stores in the cloud should be deleted after
        execution of this task or not. Set to True to avoid uploading files again
        in subsequent runs (at the cost of storing your data in the cloud). If
        files change between runs, new files are synced to the bucket.

      sky.Storage.mode: str; either of MOUNT or COPY, defaults to MOUNT
        Whether to mount the storage object by copying files, or actually
        mounting the remote storage object. With MOUNT mode, files are streamed
        from the remote object store and writes are replicated to the object
        store (and consequently, to other workers mounting the same Storage).
        With COPY mode, files are copied at VM initialization and any writes to
        the mount path will not be replicated on the object store.

.. _sky-storage:

SkyPilot Storage
=================

A SkyPilot Storage object represents an abstract data store containing large data
files required by the task. Think of it as a bucket of files that can be attached
to your task. Compared to file_mounts, storage is faster and
can persist across runs, requiring fewer uploads from your local machine.
Behind the scenes, storage automatically uploads all data in the source
to a backing object store in a particular cloud (S3/GCS/R2/IBM).

A storage object is used by "mounting" it to a task. On mounting, the data
specified in the source becomes available at the destination mount path.

A storage object can used in either :code:`MOUNT` mode or :code:`COPY` mode.

* In :code:`MOUNT` mode, the backing store is directly "mounted" to the remote VM.
  I.e., files are streamed when accessed by the task and all writes are replicated
  to remote bucket (and any other VMs mounting the same bucket).

* In :code:`COPY` mode, the files are pre-fetched and cached on the local disk.
  Writes are not replicated on the remote store.

.. note::
    sky.Storage does not guarantee preservation of file
    permissions - you may need to set file permissions during task execution.

Using SkyPilot Storage
----------------------
SkyPilot Storage can be used by specifying additional fields in the
:code:`file_mounts`. By default, :code:`file_mounts` uses rsync to
directly copy files from local to remote VM.
However, you can have them backed by SkyPilot Storage, which uploads
the files to a cloud store (e.g. S3, GCS, R2, or IBM) and have them persist there by
specifying the :code:`name`, :code:`source` and :code:`persistent` fields. By
enabling persistence, file_mount sync can be made significantly faster.

Here is an example of a :code:`file_mount` that uses SkyPilot Storage:

.. code-block:: yaml

    file_mounts:
      /mybucket:
        name: my-sky-bucket # Make sure it is unique or you own this bucket name
        source: ~/dataset # Contents of the store. Can be local or an object store path.
        store: s3 # Could be either of [s3, gcs, r2]. Defaults to None.
        persistent: True  # Set to False to delete the bucket after the task is done. Defaults to True.
        mode: MOUNT  # MOUNT or COPY. Defaults to MOUNT if not specified


Common Use Cases
^^^^^^^^^^^^^^^^

1.  **You want to upload your local data to remote VM -** specify the name and
    source fields. Name sets the bucket name that will be used, and source
    specifies the local path to be uploaded.

2.  **You want to mount an existing S3/GCS/R2 bucket to your remote VM -** specify
    just the source field (e.g., s3://my-bucket/, gs://my-bucket/ or r2://my-bucket/).

3.  **You want to have a write-able path to directly write files to cloud buckets
    -** specify a name (to create a bucket if it doesn't exist) and set the mode
    to MOUNT. This is useful for writing code outputs, such as checkpoints or
    logs directly to a cloud bucket.

4.  **You want to have a shared file-system across workers running on different
    nodes -** specify a name (to create a bucket if it doesn't exist) and set
    the mode to MOUNT. This will create an empty scratch space that workers
    can write to. Any writes will show up on all worker's mount points.

Here are a few examples covering a range of use cases for sky file_mounts
and storage mounting:

.. code-block:: yaml

    name: storage-demo

    resources:
      cloud: aws


    file_mounts:
      # *** Copying files from local ***
      #
      # This uses rsync to directly copy files from your machine to the remote VM at
      # /datasets.
      /datasets: ~/datasets

      # *** Copying files from S3 ***
      #
      # This re-uses a predefined bucket (public bucket used here, but can be
      # private) and copies its contents directly to /datasets-s3.
      /datasets-s3: s3://enriched-topical-chat

      # *** Copying files from GCS ***
      #
      # This copies a single object (train-00001-of-01024) from a remote cloud
      # storage to local disk.
      /train-00001-of-01024: gs://cloud-tpu-test-datasets/fake_imagenet/train-00001-of-01024

      # *** Copying files from IBM COS ***
      #
      # This re-uses a predefined bucket and copies its contents directly to /datasets-cos. 
      # Users must provide the region their bucket resides in, e.g. cos://us-east/bucket-name.
      /datasets-cos: cos://<region-of-bucket>/<bucket-name>

      # *** Persistent Data Storage by copying from S3 ***
      #
      # This uses SkyPilot Storage to first create a S3 bucket named sky-dataset,
      # copies the contents of ~/datasets to the remote bucket and makes the
      # bucket persistent (i.e., the bucket is not deleted after the completion of
      # this sky task, and future invocations of this bucket will be much faster).
      # When the VM is initialized, the contents of the bucket are copied to
      # /datasets-storage. If the bucket already exists, it is fetched and re-used.
      /datasets-storage:
        name: sky-dataset-romil # Make sure this name is unique or you own this bucket
        source: ~/datasets
        store: s3 # Could be either of [s3, gcs, r2, ibm]; default: None
        persistent: True  # Defaults to True, can be set to false.
        mode: COPY  # Defaults to MOUNT if not specified

      # *** Persistent Data Storage by MOUNTING S3 ***
      #
      # This uses the exact same storage object defined above, but uses the MOUNT
      # mode. This means instead of copying contents of the remote bucket to the VM,
      # sky "mounts" the bucket at /dataset-storage-mount. Files are streamed from
      # S3 as they are read by the task. Any writes made at /dataset-storage-mount
      # are also replicated on the remote S3 bucket and any other storage mounts
      # using the same bucket with MOUNT mode. Note that the source is synced with
      # the remote bucket everytime this task is run.
      /dataset-storage-mount:
        name: sky-dataset-romil
        source: ~/datasets
        mode: MOUNT

      # *** Mounting very large public buckets ***
      #
      # This uses the MOUNT mode to mount a mount at 3.5 TB public bucket at the
      # specified path. Since MOUNT mode is used, the bucket is not copied at init,
      # instead contents are streamed from S3 as they are requested. This saves disk
      # space on the remote VM.
      # Since this is a public bucket, any writes to the path will fail.
      /huge-dataset-mount:
        source: s3://digitalcorpora
        mode: MOUNT

      # *** Collecting outputs of tasks on S3 ***
      #
      # This uses the MOUNT mode to create an output mount path. This creates an
      # empty bucket with the specified name and mounts it at the path.
      # Any files written to /outputs-mount will also be synced to my-output-bucket.
      # This is useful when you want to collect outputs of your task directly in a
      # S3 bucket and browse it from your laptop later.
      #
      # Since writes are synced across workers mounting the same bucket,
      # this approach can also be used to create a shared filesystem across workers.
      # See examples/storage/pingpong.yaml for an example.
      /outputs-mount:
        name: my-output-bucket
        mode: MOUNT

      # *** Uploading multiple files to the same Storage object ***
      #
      # The source field in a storage object can also be a list of local paths.
      # This is useful when multiple files or directories need to be uploaded to the
      # same bucket.
      #
      # Note: The basenames of each path in the source list are copied recursively
      # to the root of the bucket. Thus, if the source list contains a directory,
      # the entire directory is copied to the root of the bucket. For instance,
      # in this example, the contents of ~/datasets are copied to
      # s3://sky-multisource-storage/datasets/. ~/mydir/myfile.txt will appear
      # at s3://sky-multisource-storage/myfile.txt.
      /datasets-multisource-storage:
        name: sky-multisource-storage2 # Make sure this name is unique or you own this bucket
        source: [~/mydir/myfile.txt, ~/datasets]


    run: |
      pwd
      ls -la /

    # Remember to run `sky storage ls` and `sky storage delete` to delete the
    # created storage objects!

.. note::
    Stopping a running cluster will cause any Storage mounted with :code:`MOUNT`
    mode to be unmounted. These mounts will not be re-mounted on running
    :code:`sky start`, or even :code:`sky exec`. Please run :code:`sky launch`
    again on the same cluster to ensure :code:`MOUNT` mode Storages are mounted
    again.

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

Creating a shared file system
-----------------------------

SkyPilot Storage can also be used to create a shared file-system backed by a remote object store (e.g., S3)
that multiple tasks on different nodes can read and write to. This allows developers to pass files
between workers and even use files as a medium for inter-process communication (IPC).

To create a shared filesystem, simply create a Storage object without a source
and use mount mode when attaching it to your tasks like so:

.. code-block:: yaml

    file_mounts:
      /sharedfs:
        name: my-sky-sharedfs
        mode: MOUNT


Here is a `simple example <https://github.com/skypilot-org/skypilot/blob/master/examples/storage/pingpong.yaml>`_
using SkyPilot Storage to perform communication between processes using files.


Using SkyPilot Storage CLI tools
--------------------------------

To manage persistent Storage objects, the sky CLI provides two useful commands -
:code:`sky storage ls` and :code:`sky storage delete`.

1.  :code:`sky storage ls` shows the currently provisioned Storage objects.

.. code-block:: console

    $ sky storage ls
    NAME               CREATED     STORE  COMMAND                                        STATUS
    sky-dataset-romil  3 mins ago  S3     sky launch -c demo examples/storage_demo.yaml  READY

2.  :code:`sky storage delete` allows you to delete any Storage objects managed
    by sky.

.. code-block:: console

    $ sky storage delete sky-dataset-romil
    Deleting storage object sky-dataset-romil...
    I 04-02 19:42:24 storage.py:336] Detected existing storage object, loading Storage: sky-dataset-romil
    I 04-02 19:42:26 storage.py:683] Deleting S3 Bucket sky-dataset-romil

.. note::
    :code:`sky storage ls` only shows Storage objects whose buckets were created
    by sky. Storage objects using externally managed buckets or public buckets
    are not listed in :code:`sky storage ls` and cannot be managed through sky.

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

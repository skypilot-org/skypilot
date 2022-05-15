.. _yaml-spec:
YAML Configuration
==================

Sky provides the ability to specify a task, its resource requirements, and take
advantage of many other features provided using a YAML interface. Below, we
describe all fields available.

.. code-block:: yaml

    # Task name (optional), used for display purposes.
    name: my-task

    # Working directory (optional), synced to ~/sky_workdir on the remote cluster
    # each time launch or exec is run with the yaml file.
    #
    # Commands in "setup" and "run" will be executed under it.
    #
    # If a .gitignore file (or a .git/info/exclude file) exists in the working
    # directory, files and directories listed in it will be excluded from syncing.
    workdir: ~/my-task-code

    # Number of nodes (optional; defaults to 1) to launch including the head node.
    #
    # A task can set this to a smaller value than the size of a cluster.
    num_nodes: 4

    # Per-node resource requirements (optional).
    resources:
      cloud: aws  # The cloud to use (optional).

      # The region to use (optional). The Auto-failover will be disabled
      # if this is specified.
      region: us-east-1 

      # Accelerator name and count per node (optional).
      #
      # Use `sky show-gpus` to view available accelerator configurations.
      #
      # Format: <name>:<count> (or simply <name>, short for a count of 1).
      accelerators: V100:4

      # Instance type to use (optional). If 'accelerators' is specified,
      # the corresponding instance type is automatically inferred.
      instance_type: p3.8xlarge

      # Whether the cluster should use spot instances (optional).
      # If unspecified, defaults to False (on-demand instances).
      use_spot: False

      # The recovery strategy for spot jobs (optional).
      # `use_spot` must be True for this to have any effect. For now, only
      # `FAILOVER` strategy is supported.
      spot_recovery: none

      # Disk size in GB to allocate for OS (mounted at /). Increase this if you
      # have a large working directory or tasks that write out large outputs.
      disk_size: 256

      # Additional accelerator metadata (optional); only used for TPUs.
      accelerator_args:
        tf_version: 2.5.0
        tpu_name: mytpu

    file_mounts:
      # Uses rsync to copy local files to all nodes of the cluster.
      #
      # If symlinks are present, they are copied as symlinks, and their targets
      # must also be synced using file_mounts to ensure correctness.
      /remote/path/datasets: /local/path/datasets

      # Uses Sky Storage to create a S3 bucket named sky-dataset, uploads the
      # contents of /local/path/datasets to the bucket, and marks the bucket
      # as persistent (it will not be deleted after the completion of this task).
      # Symlink contents are copied over.
      #
      # Mounts the bucket at /datasets-storage on every node of the cluster.
      /datasets-storage:
        name: sky-dataset  # Name of storage, optional when source is bucket URI
        source: /local/path/datasets  # Source path, can be local or s3/gcs URL. Optional, do not specify to create an empty bucket.
        store: s3  # Could be either 's3' or 'gcs'; default: None. Optional.
        persistent: True  # Defaults to True; can be set to false. Optional.
        mode: MOUNT  # Either MOUNT or COPY. Optional.

      # Copies a cloud object store URI to the cluster. Can be private buckets.
      /datasets-s3: s3://my-awesome-dataset

    # Setup script (optional) to execute on every `sky launch`.
    # This is executed before the 'run' commands.
    #
    # The '|' separator indicates a multiline string. To specify a single command:
    #   setup: pip install -r requirements.txt
    setup: |
      echo "Begin setup."
      pip install -r requirements.txt
      echo "Setup complete."

    # Main program (optional, but recommended) to run on every node of the cluster.
    run: |
      echo "Beginning task."
      python train.py

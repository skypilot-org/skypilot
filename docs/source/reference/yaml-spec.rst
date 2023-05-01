.. _yaml-spec:

Task YAML
==================

SkyPilot provides an intuitive YAML interface to specify a task (resource requirements, setup commands, run commands, file mounts, storage mounts, and so on).

Task YAMLs can be used with the :ref:`CLI <cli>`, or the programmatic API (:meth:`sky.Task.from_yaml`).

Available fields:

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

      # The region to use (optional). Auto-failover will be disabled
      # if this is specified.
      region: us-east-1

      # The zone to use (optional). Auto-failover will be disabled
      # if this is specified.
      zone: us-east-1a

      # Accelerator name and count per node (optional).
      #
      # Use `sky show-gpus` to view available accelerator configurations.
      #
      # Format: <name>:<count> (or simply <name>, short for a count of 1).
      accelerators: V100:4

      # Number of vCPUs per node (optional).
      #
      # Format: <count> (exactly <count> vCPUs) or <count>+
      # (at least <count> vCPUs).
      #
      # E.g., 4+ would first try to find an instance type with 4 vCPUs. If not
      # found, it will use the next cheapest instance with more than 4 vCPUs.
      cpus: 32

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

      # Disk tier to use for OS (optional).
      # Could be one of 'low', 'medium', or 'high' (default: 'medium'). Rough performance estimate:
      #   low: 500 IOPS; read 20MB/s; write 40 MB/s
      #   medium: 3000 IOPS; read 220 MB/s; write 200 MB/s
      #   high: 6000 IOPS; 340 MB/s; write 250 MB/s
      disk_tier: 'medium'

      # Additional accelerator metadata (optional); only used for TPU node
      # and TPU VM.
      # Example usage:
      #
      #   To request a TPU node:
      #     accelerator_args:
      #       tpu_name: ...
      #
      #   To request a TPU VM:
      #     accelerator_args:
      #       tpu_vm: True
      #
      # By default, the value for "runtime_version" is decided based on which is
      # requested and should work for either case. If passing in an incompatible
      # version, GCP will throw an error during provisioning.
      accelerator_args:
        # Default is "2.5.0" for TPU node and "tpu-vm-base" for TPU VM.
        runtime_version: 2.5.0
        tpu_name: mytpu
        tpu_vm: False  # False to use TPU nodes (the default); True to use TPU VMs.

      # Custom image id (optional, advanced). The image id used to boot the
      # instances. Only supported for AWS and GCP. If not specified, SkyPilot
      # will use the default debian-based image suitable for machine learning tasks.
      #
      # AWS
      # To find AWS AMI ids: https://leaherb.com/how-to-find-an-aws-marketplace-ami-image-id
      # You can also change the default OS version by choosing from the following image tags provided by SkyPilot:
      #   image_id: skypilot:gpu-ubuntu-2004
      #   image_id: skypilot:k80-ubuntu-2004
      #   image_id: skypilot:gpu-ubuntu-1804
      #   image_id: skypilot:k80-ubuntu-1804
      # It is also possible to specify a per-region image id (failover will only go through the regions sepcified as keys; 
      # useful when you have the custom images in multiple regions):
      #   image_id:
      #     us-east-1: ami-0729d913a335efca7
      #     us-west-2: ami-050814f384259894c
      image_id: ami-0868a20f5a3bf9702
      # GCP
      # To find GCP images: https://cloud.google.com/compute/docs/images
      # image_id: projects/deeplearning-platform-release/global/images/family/tf2-ent-2-1-cpu-ubuntu-2004
      #
      # IBM
      # Create a private VPC image and paste its ID in the following format:
      # image_id: <unique_image_id>
      # To create an image manually:
      # https://cloud.ibm.com/docs/vpc?topic=vpc-creating-and-using-an-image-from-volume.
      # To use an official VPC image creation tool:
      # https://www.ibm.com/cloud/blog/use-ibm-packer-plugin-to-create-custom-images-on-ibm-cloud-vpc-infrastructure
      # To use a more limited but easier to manage tool:
      # https://github.com/IBM/vpc-img-inst

    file_mounts:
      # Uses rsync to sync local files/directories to all nodes of the cluster.
      #
      # If symlinks are present, they are copied as symlinks, and their targets
      # must also be synced using file_mounts to ensure correctness.
      /remote/dir1/file: /local/dir1/file
      /remote/dir2: /local/dir2

      # Uses SkyPilot Storage to create a S3 bucket named sky-dataset, uploads the
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

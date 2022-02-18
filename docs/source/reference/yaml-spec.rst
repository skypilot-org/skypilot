YAML Configuration
==================

Sky provides the ability to specify a task, its resource requirements, and take
advantage of many other features provided using a YAML interface. Below, we
describe all fields available.

.. code-block:: yaml

    # Task name (optional), used in the job queue.
    name: my-task

    # Working directory (optional), synced each time launch or exec is run
    # with the yaml file.
    workdir: ~/my-task-code

    # Number of nodes (optional) to launch including the head node. If not
    # specified, defaults to 1. The specified resource requirements are identical
    # across all nodes.
    num_nodes: 4

    # Per-node resource requirements (optional).
    resources:
      cloud: aws # A cloud (optional) can be specified, if desired.

      # Accelerator requirements (optional) can be specified, use sky show-gpus
      # to view available accelerator configurations.
      accelerators:
        V100: 4 # Specify the accelerator type and the count per node.

      # Accelerator arguments (optional) provides additional metadata for some
      # accelerators, such as the TensorFlow version for TPUs.
      accelerator_args:
        tf_version: 2.5.0

      # Specify whether the cluster should use spot instances or not (optional).
      # If unspecified, Sky will default to on-demand instances.
      use_spot: False

    # Using Sky Storage, you can specify file mounts (all optional).
    file_mounts:
      # This uses rsync to directly copy files from your machine to the remote
      # VM at /datasets.
      /datasets: ~/datasets

      # This uses Sky Storage to first create a S3 bucket named sky-dataset,
      # copies the contents of ~/datasets to the remote bucket and makes the
      # bucket persistent (i.e., the bucket is not deleted after the completion of
      # this sky task, and future invocations of this bucket will be much faster).
      # The bucket is mounted at /datasets-storage.
      /datasets-storage:
        name: sky-dataset
        source: ~/datasets
        force_stores: [s3] # Could be [s3, gcs], [gcs] default: None
        persistent: True  # Defaults to True, can be set to false.

      # This re-uses a predefined bucket (sky-dataset, defined above) and mounts it
      # directly at datasets-s3.
      /datasets-s3: s3://sky-dataset

    # A setup script (optional) can be provided to run when a cluster is provisioned or a
    # task is launched.
    setup: |
      echo "Begin setup."
      pip install -r requirements.txt
      echo "Setup complete."

    # Alternatively, a single setup command (optional) can be provided.
    setup: pip install -r requirements.txt

    # A task script (optional, but recommended) is the main script to run on the
    # cluster.
    run: |
      echo "Beginning task."
      python train.py

    # Alternatively, a single run command can be provided.
    run: python train.py

.. _yaml-spec:

SkyPilot YAML
=============

SkyPilot provides an intuitive YAML interface to specify clusters, jobs, or services (resource requirements, setup commands, run commands, file mounts, storage mounts, and so on).

**All fields in the YAML specification are optional.** When unspecified, its
default value is used. You can specify only the fields that are relevant to
your task.

YAMLs can be used with the :ref:`CLI <cli>`, or the programmatic API (e.g., :meth:`sky.Task.from_yaml`).


Syntax
------

Below is the configuration syntax and some example values.  See details under each field.

.. parsed-literal::

  :ref:`name <yaml-spec-name>`: my-task

  :ref:`workdir <yaml-spec-workdir>`: ~/my-task-code

  :ref:`num_nodes <yaml-spec-num-nodes>`: 4

  :ref:`resources <yaml-spec-resources>`:
    # Location.
    :ref:`cloud <yaml-spec-resources-cloud>`: aws
    :ref:`region <yaml-spec-resources-region>`: us-east-1
    :ref:`zone <yaml-spec-resources-zone>`: us-east-1a

    # Hardware.
    :ref:`accelerators <yaml-spec-resources-accelerators>`: H100:8
    :ref:`accelerator_args <yaml-spec-resources-accelerator-args>`:
      runtime_version: tpu-vm-base
    :ref:`cpus <yaml-spec-resources-cpus>`: 4+
    :ref:`memory <yaml-spec-resources-memory>`: 32+
    :ref:`instance_type <yaml-spec-resources-instance-type>`: p3.8xlarge
    :ref:`use_spot <yaml-spec-resources-use-spot>`: false
    :ref:`disk_size <yaml-spec-resources-disk-size>`: 256
    :ref:`disk_tier <yaml-spec-resources-disk-tier>`: medium

    # Config.
    :ref:`image_id <yaml-spec-resources-image-id>`: ami-0868a20f5a3bf9702
    :ref:`ports <yaml-spec-resources-ports>`: 8081
    :ref:`labels <yaml-spec-resources-labels>`:
      my-label: my-value

    :ref:`any_of <yaml-spec-resources-any-of>`:
      - cloud: aws
        region: us-west-2
        accelerators: H100
      - cloud: gcp
        accelerators: H100

    :ref:`ordered <yaml-spec-resources-ordered>`:
      - cloud: aws
        region: us-east-1
      - cloud: aws
        region: us-west-2

    :ref:`job_recovery <yaml-spec-resources-job-recovery>`: none

  :ref:`envs <yaml-spec-envs>`:
    MY_BUCKET: skypilot-temp-gcs-test
    MY_LOCAL_PATH: tmp-workdir
    MODEL_SIZE: 13b

  :ref:`file_mounts <yaml-spec-file-mounts>`:
    /remote/path: /local/path
    /checkpoints:
      source: s3://existing-bucket
      mode: MOUNT
    /datasets-s3: s3://my-awesome-dataset

  :ref:`setup <yaml-spec-setup>`: |
    echo "Begin setup."
    pip install -r requirements.txt
    echo "Setup complete."

  :ref:`run <yaml-spec-run>`: |
    echo "Begin run."
    python train.py
    echo Env var MODEL_SIZE has value: ${MODEL_SIZE}

  :ref:`config <yaml-spec-config>`:
    kubernetes:
      provision_timeout: 600

Fields
----------

.. _yaml-spec-name:

``name``
~~~~~~~~

Task name (optional), used for display purposes.

.. code-block:: yaml

  name: my-task

.. _yaml-spec-workdir:

``workdir``
~~~~~~~~~~~

Working directory (optional), synced to ``~/sky_workdir`` on the remote cluster each time launch or exec is run with the yaml file.

Commands in ``setup`` and ``run`` will be executed under it.

If a relative path is used, it's evaluated relative to the location from which ``sky`` is called.

To exclude files from syncing, see https://docs.skypilot.co/en/latest/examples/syncing-code-artifacts.html#exclude-uploading-files

.. code-block:: yaml

  workdir: ~/my-task-code

OR

.. code-block:: yaml

  workdir: ../my-project  # Relative path


.. _yaml-spec-num-nodes:

``num_nodes``
~~~~~~~~~~~~~

Number of nodes (optional; defaults to 1) to launch including the head node.

A task can set this to a smaller value than the size of a cluster.

.. code-block:: yaml

  num_nodes: 4


.. _yaml-spec-resources:

``resources``
~~~~~~~~~~~~~

Per-node resource requirements (optional).

.. code-block:: yaml

  resources:
    cloud: aws
    instance_type: p3.8xlarge


.. _yaml-spec-resources-cloud:

``resources.cloud``
~~~~~~~~~~~~~~~~~~~

The cloud to use (optional).

.. code-block:: yaml

  resources:
    cloud: aws

OR

.. code-block:: yaml

  resources:
    cloud: gcp


.. _yaml-spec-resources-region:

``resources.region``
~~~~~~~~~~~~~~~~~~~~

The region to use (optional).

Auto-failover will be disabled if this is specified.

.. code-block:: yaml

  resources:
    region: us-east-1


.. _yaml-spec-resources-zone:

``resources.zone``
~~~~~~~~~~~~~~~~~~

The zone to use (optional).

Auto-failover will be disabled if this is specified.

.. code-block:: yaml

  resources:
    zone: us-east-1a


.. _yaml-spec-resources-accelerators:

``resources.accelerators``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Accelerator name and count per node (optional).

Use ``sky show-gpus`` to view available accelerator configurations.

The following three ways are valid for specifying accelerators for a cluster:

- To specify a single type of accelerator:

  Format: ``<name>:<count>`` (or simply ``<name>``, short for a count of 1).

  Example: ``H100:4``

- To specify an ordered list of accelerators (try the accelerators in the specified order):

  Format: ``[<name>:<count>, ...]``

  Example: ``['L4:1', 'H100:1', 'A100:1']``

- To specify an unordered set of accelerators (optimize all specified accelerators together, and try accelerator with lowest cost first):

  Format: ``{<name>:<count>, ...}``

  Example: ``{'L4:1', 'H100:1', 'A100:1'}``

.. code-block:: yaml

  resources:
    accelerators: V100:8

OR

.. code-block:: yaml

  resources:
    accelerators:
      - A100:1
      - V100:1

OR

.. code-block:: yaml

  resources:
    accelerators: {A100:1, V100:1}


.. _yaml-spec-resources-accelerator-args:

``resources.accelerator_args``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Additional accelerator metadata (optional); only used for TPU node and TPU VM.

Example usage:

- To request a TPU VM:

  .. code-block:: yaml

    resources:
      accelerator_args:
        tpu_vm: true  # optional, default: True

- To request a TPU node:

  .. code-block:: yaml

    resources:
      accelerator_args:
        tpu_name: mytpu
        tpu_vm: false

By default, the value for ``runtime_version`` is decided based on which is requested and should work for either case. If passing in an incompatible version, GCP will throw an error during provisioning.

Example:

.. code-block:: yaml

  resources:
    accelerator_args:
      # Default is "tpu-vm-base" for TPU VM and "2.12.0" for TPU node.
      runtime_version: tpu-vm-base
      # tpu_name: mytpu
      # tpu_vm: false  # True to use TPU VM (the default); False to use TPU node.



.. _yaml-spec-resources-cpus:

``resources.cpus``
~~~~~~~~~~~~~~~~~~

Number of vCPUs per node (optional).

Format:

- ``<count>``: exactly ``<count>`` vCPUs
- ``<count>+``: at least ``<count>`` vCPUs

Example: ``4+`` means first try to find an instance type with >= 4 vCPUs. If not found, use the next cheapest instance with more than 4 vCPUs.

.. code-block:: yaml

  resources:
    cpus: 4+

OR

.. code-block:: yaml

  resources:
    cpus: 16


.. _yaml-spec-resources-memory:

``resources.memory``
~~~~~~~~~~~~~~~~~~~~

Memory in GiB per node (optional).

Format:

-  ``<num>``: exactly ``<num>`` GiB
-  ``<num>+``: at least ``<num>`` GiB

Example: ``32+`` means first try to find an instance type with >= 32 GiB. If not found, use the next cheapest instance with more than 32 GiB.

.. code-block:: yaml

  resources:
    memory: 32+

OR

.. code-block:: yaml

  resources:
    memory: 64

.. _yaml-spec-resources-instance-type:

``resources.instance_type``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Instance type to use (optional).

If ``accelerators`` is specified, the corresponding instance type is automatically inferred.

.. code-block:: yaml

  resources:
    instance_type: p3.8xlarge


.. _yaml-spec-resources-use-spot:

``resources.use_spot``
~~~~~~~~~~~~~~~~~~~~~~

Whether the cluster should use spot instances (optional).

If unspecified, defaults to ``false`` (on-demand instances).

.. code-block:: yaml

  resources:
    use_spot: true


.. _yaml-spec-resources-disk-size:

``resources.disk_size``
~~~~~~~~~~~~~~~~~~~~~~~

Disk size in GB to allocate for OS (mounted at ``/``).

Increase this if you have a large working directory or tasks that write out large outputs.

.. code-block:: yaml

  resources:
    disk_size: 256


.. _yaml-spec-resources-disk-tier:

``resources.disk_tier``
~~~~~~~~~~~~~~~~~~~~~~~
Disk tier to use for OS (optional).

Could be one of ``'low'``, ``'medium'``, ``'high'``, ``'ultra'`` or ``'best'`` (default: ``'medium'``).

If ``'best'`` is specified, use the best disk tier enabled.

Rough performance estimate:

- low: 1000 IOPS; read 90 MB/s; write 90 MB/s
- medium: 3000 IOPS; read 220 MB/s; write 220 MB/s
- high: 6000 IOPS; read 400 MB/s; write 400 MB/s
- ultra: 60000 IOPS;  read 4000 MB/s; write 3000 MB/s

Measured by ``examples/perf/storage_rawperf.yaml``

.. code-block:: yaml

  resources:
    disk_tier: medium

OR

.. code-block:: yaml

  resources:
    disk_tier: best


.. _yaml-spec-resources-ports:

``resources.ports``
~~~~~~~~~~~~~~~~~~~

Ports to expose (optional).

All ports specified here will be exposed to the public Internet. Under the hood, a firewall rule / inbound rule is automatically added to allow inbound traffic to these ports.

Applies to all VMs of a cluster created with this field set.

Currently only TCP protocol is supported.

Ports Lifecycle:

A cluster's ports will be updated whenever ``sky launch`` is executed. When launching an existing cluster, any new ports specified will be opened for the cluster, and the firewall rules for old ports will never be removed until the cluster is terminated.

Could be an integer, a range, or a list of integers and ranges:

- To specify a single port: ``8081``
- To specify a port range: ``10052-10100``
- To specify multiple ports / port ranges:

.. code-block:: yaml

  resources:
  ports:
    - 8080
    - 10022-10040

OR

.. code-block:: yaml

  resources:
    ports: 8081

OR

.. code-block:: yaml

  resources:
    ports: 10052-10100

OR

.. code-block:: yaml

  resources:
    ports:
      - 8080
      - 10022-10040


.. _yaml-spec-resources-image-id:

``resources.image_id``
~~~~~~~~~~~~~~~~~~~~~~
Custom image id (optional, advanced).

The image id used to boot the instances. Only supported for AWS, GCP, OCI and IBM (for non-docker image).

If not specified, SkyPilot will use the default debian-based image suitable for machine learning tasks.

**Docker support**

You can specify docker image to use by setting the image_id to ``docker:<image name>`` for Azure, AWS and GCP. For example,

.. code-block:: yaml

  resources:
    image_id: docker:ubuntu:latest

Currently, only debian and ubuntu images are supported.

If you want to use a docker image in a private registry, you can specify your username, password, and registry server as task environment variable. For details, please refer to the ``envs`` section below.

**AWS**

To find AWS AMI ids: https://leaherb.com/how-to-find-an-aws-marketplace-ami-image-id

You can also change the default OS version by choosing from the following image tags provided by SkyPilot:

.. code-block:: yaml

  resources:
    image_id: skypilot:gpu-ubuntu-2004
    image_id: skypilot:k80-ubuntu-2004
    image_id: skypilot:gpu-ubuntu-1804
    image_id: skypilot:k80-ubuntu-1804

It is also possible to specify a per-region image id (failover will only go through the regions specified as keys; useful when you have the custom images in multiple regions):

.. code-block:: yaml

  resources:
    image_id:
      us-east-1: ami-0729d913a335efca7
      us-west-2: ami-050814f384259894c

**GCP**

To find GCP images: https://cloud.google.com/compute/docs/images

.. code-block:: yaml

  resources:
    image_id: projects/deeplearning-platform-release/global/images/common-cpu-v20230615-debian-11-py310

Or machine image: https://cloud.google.com/compute/docs/machine-images

.. code-block:: yaml

  resources:
    image_id: projects/my-project/global/machineImages/my-machine-image

**Azure**

To find Azure images: https://docs.microsoft.com/en-us/azure/virtual-machines/linux/cli-ps-findimage

.. code-block:: yaml

  resources:
    image_id: microsoft-dsvm:ubuntu-2004:2004:21.11.04

**OCI**

To find OCI images: https://docs.oracle.com/en-us/iaas/images

You can choose the image with OS version from the following image tags provided by SkyPilot:

.. code-block:: yaml

  resources:
    image_id: skypilot:gpu-ubuntu-2204
    image_id: skypilot:gpu-ubuntu-2004
    image_id: skypilot:gpu-oraclelinux9
    image_id: skypilot:gpu-oraclelinux8
    image_id: skypilot:cpu-ubuntu-2204
    image_id: skypilot:cpu-ubuntu-2004
    image_id: skypilot:cpu-oraclelinux9
    image_id: skypilot:cpu-oraclelinux8

It is also possible to specify your custom image's OCID with OS type, for example:

.. code-block:: yaml

  resources:
    image_id: ocid1.image.oc1.us-sanjose-1.aaaaaaaaywwfvy67wwe7f24juvjwhyjn3u7g7s3wzkhduxcbewzaeki2nt5q:oraclelinux
    image_id: ocid1.image.oc1.us-sanjose-1.aaaaaaaa5tnuiqevhoyfnaa5pqeiwjv6w5vf6w4q2hpj3atyvu3yd6rhlhyq:ubuntu

**IBM**

Create a private VPC image and paste its ID in the following format:

.. code-block:: yaml

  resources:
    image_id: <unique_image_id>

To create an image manually:
https://cloud.ibm.com/docs/vpc?topic=vpc-creating-and-using-an-image-from-volume.

To use an official VPC image creation tool:
https://www.ibm.com/cloud/blog/use-ibm-packer-plugin-to-create-custom-images-on-ibm-cloud-vpc-infrastructure

To use a more limited but easier to manage tool:
https://github.com/IBM/vpc-img-inst

.. code-block:: yaml

  resources:
    image_id: ami-0868a20f5a3bf9702  # AWS example
    # image_id: projects/deeplearning-platform-release/global/images/common-cpu-v20230615-debian-11-py310  # GCP example
    # image_id: docker:pytorch/pytorch:1.13.1-cuda11.6-cudnn8-runtime # Docker example

OR

.. code-block:: yaml

  resources:
    image_id:
      us-east-1: ami-123
      us-west-2: ami-456

.. _yaml-spec-resources-labels:

``resources.labels``
~~~~~~~~~~~~~~~~~~~~
Labels to apply to the instances (optional).

If specified, these labels will be applied to the VMs or pods created by SkyPilot.

These are useful for assigning metadata that may be used by external tools.

Implementation differs by cloud provider:

- AWS: Labels are mapped to instance tags
- GCP: Labels are mapped to instance labels
- Kubernetes: Labels are mapped to pod labels
- Other: Labels are not supported and will be ignored

Note: Labels are applied only on the first launch of the cluster. They are not updated on subsequent launches.

Example:

.. code-block:: yaml

  resources:
    labels:
      project: my-project
      department: research


.. _yaml-spec-resources-any-of:

``resources.any_of``
~~~~~~~~~~~~~~~~~~~~
Candidate resources (optional).

If specified, SkyPilot will only use these candidate resources to launch the cluster.

The fields specified outside of ``any_of`` will be used as the default values for all candidate resources, and any duplicate fields specified inside ``any_of`` will override the default values.

``any_of`` means that SkyPilot will try to find a resource that matches any of the candidate resources, i.e. the failover order will be decided by the optimizer.

Example:

.. code-block:: yaml

  resources:
    any_of:
      - cloud: aws
        region: us-west-2
        accelerators: H100
      - cloud: gcp
        accelerators: H100

.. _yaml-spec-resources-ordered:

``resources.ordered``
~~~~~~~~~~~~~~~~~~~~~~
Ordered candidate resources (optional).

If specified, SkyPilot will failover through the candidate resources with the specified order.

The fields specified outside of ``ordered`` will be used as the default values for all candidate resources, and any duplicate fields specified inside ``ordered`` will override the default values.

``ordered`` means that SkyPilot will failover through the candidate resources with the specified order.

Example:

.. code-block:: yaml

  resources:
    ordered:
      - cloud: aws
        region: us-east-1
      - cloud: aws
        region: us-west-2

.. _yaml-spec-resources-job-recovery:

``resources.job_recovery``
~~~~~~~~~~~~~~~~~~~~~~~~~~
The recovery strategy for managed jobs (optional).

In effect for managed jobs. Possible values are ``FAILOVER`` and ``EAGER_NEXT_REGION``.

If ``FAILOVER`` is specified, the job will be restarted in the same region if the node fails, and go to the next region if no available resources are found in the same region.

If ``EAGER_NEXT_REGION`` is specified, the job will go to the next region directly if the node fails. This is useful for spot instances, as in practice, preemptions in a region usually indicate a shortage of resources in that region.

Default: ``EAGER_NEXT_REGION``

Example:

.. code-block:: yaml

  resources:
    job_recovery:
      strategy: FAILOVER

OR

.. code-block:: yaml

  resources:
    job_recovery:
      strategy: EAGER_NEXT_REGION
      max_restarts_on_errors: 3


.. _yaml-spec-envs:

``envs``
~~~~~~~~

Environment variables (optional).

These values can be accessed in the ``file_mounts``, ``setup``, and ``run`` sections below.

Values set here can be overridden by a CLI flag: ``sky launch/exec --env ENV=val`` (if ``ENV`` is present).

For costumized non-root docker image in RunPod, you need to set ``SKYPILOT_RUNPOD_DOCKER_USERNAME`` to specify the login username for the docker image. See :ref:`docker-containers-as-runtime-environments` for more.

If you want to use a docker image as runtime environment in a private registry, you can specify your username, password, and registry server as task environment variable.  For example:

.. code-block:: yaml

  envs:
    SKYPILOT_DOCKER_USERNAME: <username>
    SKYPILOT_DOCKER_PASSWORD: <password>
    SKYPILOT_DOCKER_SERVER: <registry server>

SkyPilot will execute ``docker login --username <username> --password <password> <registry server>`` before pulling the docker image. For ``docker login``, see https://docs.docker.com/engine/reference/commandline/login/

You could also specify any of them through the CLI flag if you don't want to store them in your yaml file or if you want to generate them for constantly changing password. For example:

.. code-block:: yaml

  sky launch --env SKYPILOT_DOCKER_PASSWORD=$(aws ecr get-login-password --region us-east-1).

For more information about docker support in SkyPilot, please refer to the ``image_id`` section above.

Example of using envs:

.. code-block:: yaml

  envs:
    MY_BUCKET: skypilot-data
    MODEL_SIZE: 13b
    MY_LOCAL_PATH: tmp-workdir

.. _yaml-spec-file-mounts:

``file_mounts``
~~~~~~~~~~~~~~~

File mounts configuration.

Example:

.. code-block:: yaml

  file_mounts:
    # Uses rsync to sync local files/directories to all nodes of the cluster.
    #
    # If a relative path is used, it's evaluated relative to the location from
    # which `sky` is called.
    #
    # If symlinks are present, they are copied as symlinks, and their targets
    # must also be synced using file_mounts to ensure correctness.
    /remote/dir1/file: /local/dir1/file
    /remote/dir2: /local/dir2

    # Create a S3 bucket named sky-dataset, uploads the contents of
    # /local/path/datasets to the bucket, and marks the bucket as persistent
    # (it will not be deleted after the completion of this task).
    # Symlinks and their contents are NOT copied.
    #
    # Mounts the bucket at /datasets-storage on every node of the cluster.
    /datasets-storage:
      name: sky-dataset  # Name of storage, optional when source is bucket URI
      source: /local/path/datasets  # Source path, can be local or bucket URI. Optional, do not specify to create an empty bucket.
      store: s3  # Could be either 's3', 'gcs', 'azure', 'r2', 'oci', or 'ibm'; default: None. Optional.
      persistent: True  # Defaults to True; can be set to false to delete bucket after cluster is downed. Optional.
      mode: MOUNT  # MOUNT or COPY or MOUNT_CACHED. Defaults to MOUNT. Optional.

    # Copies a cloud object store URI to the cluster. Can be private buckets.
    /datasets-s3: s3://my-awesome-dataset

    # Demoing env var usage.
    /checkpoint/${MODEL_SIZE}: ~/${MY_LOCAL_PATH}
    /mydir:
      name: ${MY_BUCKET}  # Name of the bucket.
      mode: MOUNT

OR

.. code-block:: yaml

  file_mounts:
    /remote/data: ./local_data  # Local to remote
    /remote/output: s3://my-bucket/outputs  # Cloud storage
    /remote/models:
      name: my-models-bucket
      source: ~/local_models
      store: gcs
      mode: MOUNT

.. _yaml-spec-setup:

``setup``
~~~~~~~~~

Setup script (optional) to execute on every ``sky launch``.

This is executed before the ``run`` commands.

Example:

To specify a single command:

.. code-block:: yaml

  setup: pip install -r requirements.txt

The ``|`` separator indicates a multiline string.

.. code-block:: yaml

  setup: |
    echo "Begin setup."
    pip install -r requirements.txt
    echo "Setup complete."

OR

.. code-block:: yaml

  setup: |
    conda create -n myenv python=3.9 -y
    conda activate myenv
    pip install torch torchvision

.. _yaml-spec-run:

``run``
~~~~~~~

Main program (optional, but recommended) to run on every node of the cluster.

Example:

.. code-block:: yaml

  run: |
    echo "Beginning task."
    python train.py

    # Demoing env var usage.
    echo Env var MODEL_SIZE has value: ${MODEL_SIZE}

OR

.. code-block:: yaml

  run: |
    conda activate myenv
    python my_script.py --data-dir /remote/data --output-dir /remote/output


.. _yaml-spec-config:
.. _task-yaml-experimental:

``config``
~~~~~~~~~~

:ref:`Advanced configuration options <config-client-job-task-yaml>` to apply to the task.

Example:

.. code-block:: yaml

  config:
    docker:
      run_options: ...
    kubernetes:
      pod_config: ...
      provision_timeout: ...
    gcp:
      managed_instance_group: ...
    nvidia_gpus:
      disable_ecc: ...

.. _service-yaml-spec:

SkyServe Service
================

To define a YAML for use for :ref:services <sky-serve>, use previously mentioned fields to describe each replica, then add a service section to describe the entire service.

Syntax

.. parsed-literal::

  service:
    :ref:`readiness_probe <yaml-spec-service-readiness-probe>`:
      :ref:`path <yaml-spec-service-readiness-probe-path>`: /v1/models
      :ref:`post_data <yaml-spec-service-readiness-probe-post-data>`: {'model_name': 'model'}
      :ref:`initial_delay_seconds <yaml-spec-service-readiness-probe-initial-delay-seconds>`: 1200
      :ref:`timeout_seconds <yaml-spec-service-readiness-probe-timeout-seconds>`: 15

    :ref:`readiness_probe <yaml-spec-service-readiness-probe>`: /v1/models

    :ref:`replica_policy <yaml-spec-service-replica-policy>`:
      :ref:`min_replicas <yaml-spec-service-replica-policy-min-replicas>`: 1
      :ref:`max_replicas <yaml-spec-service-replica-policy-max-replicas>`: 3
      :ref:`target_qps_per_replica <yaml-spec-service-replica-policy-target-qps-per-replica>`: 5
      :ref:`upscale_delay_seconds <yaml-spec-service-replica-policy-upscale-delay-seconds>`: 300
      :ref:`downscale_delay_seconds <yaml-spec-service-replica-policy-downscale-delay-seconds>`: 1200

    :ref:`replicas <yaml-spec-service-replicas>`: 2

  resources:
    :ref:`ports <yaml-spec-service-resources-ports>`: 8080


Fields
----------

.. _yaml-spec-service-readiness-probe:

``service.readiness_probe``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Readiness probe configuration (required).

Used by SkyServe to check if your service replicas are ready for accepting traffic.

If the readiness probe returns a 200, SkyServe will start routing traffic to that replica.

Can be defined as a path string (for GET requests with defaults) or a detailed dictionary.

.. code-block:: yaml

  service:
    readiness_probe: /v1/models

OR

.. code-block:: yaml

  service:
    readiness_probe:
      path: /v1/models
      post_data: '{"model_name": "my_model"}'
      initial_delay_seconds: 600
      timeout_seconds: 10


.. _yaml-spec-service-readiness-probe-path:

``service.readiness_probe.path``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Endpoint path for readiness checks (required).

Path to probe. SkyServe sends periodic requests to this path after the initial delay.

.. code-block:: yaml

  service:
    readiness_probe:
      path: /v1/models


.. _yaml-spec-service-readiness-probe-post-data:

``service.readiness_probe.post_data``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

POST request payload (optional).

If this is specified, the readiness probe will use POST instead of GET, and the post data will be sent as the request body.

.. code-block:: yaml

  service:
    readiness_probe:
      path: /v1/models
      post_data: '{"model_name": "my_model"}'

.. _yaml-spec-service-readiness-probe-initial-delay-seconds:

``service.readiness_probe.initial_delay_seconds``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Grace period before initiating health checks (default: 1200).

Initial delay in seconds. Any readiness probe failures during this period will be ignored.

This is highly related to your service, so it is recommended to set this value based on your service's startup time.


.. code-block:: yaml

  service:
    readiness_probe:
      initial_delay_seconds: 600

.. _yaml-spec-service-readiness-probe-timeout-seconds:

``service.readiness_probe.timeout_seconds``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Maximum wait time per probe request (default: 15).

The Timeout in seconds for a readiness probe request.

If the readiness probe takes longer than this time to respond, the probe will be considered as failed.

This is useful when your service is slow to respond to readiness probe requests.

Note, having a too high timeout will delay the detection of a real failure of your service replica.

.. code-block:: yaml

    service:
      readiness_probe:
        timeout_seconds: 10


.. _yaml-spec-service-replica-policy:

``service.replica_policy``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Autoscaling configuration for service replicas (one of replica_policy or replicas is required).

Describes how SkyServe autoscales your service based on the QPS (queries per second) of your service.

.. code-block:: yaml

    service:
      replica_policy:
        min_replicas: 1
        max_replicas: 5
        target_qps_per_replica: 10

.. _yaml-spec-service-replica-policy-min-replicas:

``service.replica_policy.min_replicas``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Minimum number of active replicas (required).

Service never scales below this count.

.. code-block:: yaml

  service:
    replica_policy:
      min_replicas: 1


.. _yaml-spec-service-replica-policy-max-replicas:

``service.replica_policy.max_replicas``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Maximum allowed replicas (optional).

If not specified, SkyServe will use a fixed number of replicas (the same as min_replicas) and ignore any QPS threshold specified below.

.. code-block:: yaml

  service:
    replica_policy:
      max_replicas: 3


.. _yaml-spec-service-replica-policy-target-qps-per-replica:

``service.replica_policy.target_qps_per_replica``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Target queries per second per replica (optional).

SkyServe will scale your service so that, ultimately, each replica manages approximately ``target_qps_per_replica`` queries per second.

**Autoscaling will only be enabled if this value is specified.**

.. code-block:: yaml

  service:
    replica_policy:
      target_qps_per_replica: 5


.. _yaml-spec-service-replica-policy-upscale-delay-seconds:

``service.replica_policy.upscale_delay_seconds``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Stabilization period before adding replicas (default: 300).

Upscale delay in seconds. To avoid aggressive autoscaling, SkyServe will only upscale your service if the QPS of your service is higher than the target QPS for a period of time.

.. code-block:: yaml

  service:
    replica_policy:
      upscale_delay_seconds: 300


.. _yaml-spec-service-replica-policy-downscale-delay-seconds:

``service.replica_policy.downscale_delay_seconds``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Cooldown period before removing replicas (default: 1200).

Downscale delay in seconds. To avoid aggressive autoscaling, SkyServe will only downscale your service if the QPS of your service is lower than the target QPS for a period of time.

.. code-block:: yaml

  service:
    replica_policy:
      downscale_delay_seconds: 1200


.. _yaml-spec-service-replicas:

``service.replicas``
~~~~~~~~~~~~~~~~~~~~

Fixed replica count alternative to autoscaling.

Simplified version of replica policy that uses a fixed number of replicas.

.. code-block:: yaml

  service:
    replicas: 2


.. _yaml-spec-service-resources-ports:

``resources.ports``
~~~~~~~~~~~~~~~~~~~

Required exposed port for service traffic.

Port to run your service on each replica.

.. code-block:: yaml

  resources:
    ports: 8080

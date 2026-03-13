<!-- AUTO-GENERATED from docs/source/reference/yaml-spec.rst -->
<!-- Run: python skills/skypilot/scripts/generate_references.py -->


# SkyPilot YAML

SkyPilot provides an intuitive YAML interface to specify clusters, jobs, or services (resource requirements, setup commands, run commands, file mounts, storage mounts, and so on).

**All fields in the YAML specification are optional.** When unspecified, its
default value is used. You can specify only the fields that are relevant to
your task.

YAMLs can be used with the CLI, or the programmatic API (e.g., `sky.Task.from_yaml()`).


## Syntax

Below is the configuration syntax and some example values.  See details under each field.

```yaml
name: my-task

workdir: ~/my-task-code

num_nodes: 4

resources:
  # Infra to use. Click to see schema and example values.
  infra: aws

  # Hardware.
  accelerators: H100:8
  accelerator_args:
    runtime_version: tpu-vm-base
  cpus: 4+
  memory: 32+
  instance_type: p3.8xlarge
  use_spot: false
  disk_size: 256
  disk_tier: medium
  network_tier: best

  # Config.
  image_id: ami-0868a20f5a3bf9702
  ports: 8081
  labels:
    my-label: my-value
  autostop:
    idle_minutes: 10
    wait_for: none
    hook: |
      cd my-code-base
      git add .
      git commit -m "Auto-commit before shutdown"
      git push
    hook_timeout: 300

  any_of:
    - infra: aws/us-west-2
      accelerators: H100
    - infra: gcp/us-central1
      accelerators: H100

  ordered:
    - infra: aws/us-east-1
    - infra: aws/us-west-2

  job_recovery: none

envs:
  MY_BUCKET: skypilot-temp-gcs-test
  MY_LOCAL_PATH: tmp-workdir
  MODEL_SIZE: 13b

secrets:
  MY_HF_TOKEN: my-secret-value
  WANDB_API_KEY: my-secret-value-2

volumes:
  /mnt/data: volume-name
  /mnt/cache:
    size: 100Gi

file_mounts:
  # Sync a local directory to a remote directory
  /remote/path: /local/path
  # Mount a S3 bucket to a remote directory
  /checkpoints:
    source: s3://existing-bucket
    mode: MOUNT
  /datasets-s3: s3://my-awesome-dataset

setup: |
  echo "Begin setup."
  pip install -r requirements.txt
  echo "Setup complete."

run: |
  echo "Begin run."
  python train.py
  echo Env var MODEL_SIZE has value: ${MODEL_SIZE}

config:
  kubernetes:
    provision_timeout: 600
```

## Fields


### ``name``

Task name (optional), used for display purposes.

```yaml
name: my-task
```


### ``workdir``

`workdir` can be a local working directory or a git repository (optional). It is synced or cloned to `~/sky_workdir` on the remote cluster each time `sky launch` or `sky exec` is run with the YAML file.

**Local Directory**:

If `workdir` is a local path, the entire directory is synced to the remote cluster. To exclude files from syncing, see exclude-uploading-files.

If a relative path is used, it's evaluated relative to the location from which `sky` is called.

**Git Repository**:

If `workdir` is a git repository, the `url` field is required and can be in one of the following formats:

* HTTPS: `https://github.com/skypilot-org/skypilot.git`
* SSH: `ssh://git@github.com/skypilot-org/skypilot.git`
* SCP: `git@github.com:skypilot-org/skypilot.git`

The `ref` field specifies the git reference to checkout, which can be:

* A branch name (e.g., `main`, `develop`)
* A tag name (e.g., `v1.0.0`)
* A commit hash (e.g., `abc123def456`)

**Authentication for Private Repositories**:

*For HTTPS URLs*: Set the `GIT_TOKEN` environment variable. SkyPilot will automatically use this token for authentication.

*For SSH/SCP URLs*: SkyPilot will attempt to authenticate using SSH keys in the following order:

1. SSH key specified by the `GIT_SSH_KEY_PATH` environment variable
2. SSH key configured in `~/.ssh/config` for the git host
3. Default SSH key at `~/.ssh/id_rsa`
4. Default SSH key at `~/.ssh/id_ed25519` (if `~/.ssh/id_rsa` does not exist)

Commands in `setup` and `run` will be executed under `~/sky_workdir`.

```yaml
workdir: ~/my-task-code
```

OR

```yaml
workdir: ../my-project  # Relative path
```

OR

```yaml
workdir:
  url: https://github.com/skypilot-org/skypilot.git
  ref: main
```


### ``num_nodes``

Number of nodes (optional; defaults to 1) to launch including the head node.

A task can set this to a smaller value than the size of a cluster.

```yaml
num_nodes: 4

```


### ``resources``

Per-node resource requirements (optional).

```yaml
resources:
  infra: aws
  instance_type: p3.8xlarge

```


### ``resources.infra``


Infrastructure to use (optional).

Schema: `<cloud>/<region>/<zone>` (region
and zone are optional), or `k8s/<context-name>` (context-name is optional).
Wildcards are supported in any component.

Example values: `aws`, `aws/us-east-1`, `aws/us-east-1/us-east-1a`,
`aws/*/us-east-1a`, `k8s`, `k8s/my-cluster-context`.

```yaml
resources:
  infra: aws  # Use any available AWS region/zone.

```

```yaml
resources:
  infra: k8s  # Use any available Kubernetes context.
```

You can also specify a specific region, zone, or Kubernetes context.

```yaml
resources:
  infra: aws/us-east-1

```

```yaml
resources:
  infra: aws/us-east-1/us-east-1a

```

```yaml
resources:
  infra: k8s/my-h100-cluster-context

```


### ``resources.autostop``

Autostop configuration (optional).

Controls whether and when to automatically stop or tear down the cluster after it becomes idle. See auto-stop for more details.

Format:

- `true`: Use default idle minutes (5)
- `false`: Disable autostop
- `<num>`: Stop after this many idle minutes
- `<num><unit>`: Stop after this much time
- Object with configuration:

  - `idle_minutes`: Number of idle minutes before stopping
  - `down`: If true, tear down the cluster instead of stopping it
  - `wait_for`: Determines the condition for resetting the idleness timer.
    Options:

    - `jobs_and_ssh` (default): Wait for in‑progress jobs and SSH connections to finish
    - `jobs`: Only wait for in‑progress jobs
    - `none`: Wait for nothing; autostop right after `idle_minutes`
  - `hook`: Optional script to execute before autostop. The script runs on the remote cluster before stopping or tearing down. If the hook fails, autostop will still proceed but a warning will be logged.

    See Autostop hooks for detailed explanation and examples.

  - `hook_timeout`: Timeout in seconds for hook execution (default: 3600 = 1 hour, minimum: 1).
    If the hook exceeds this timeout, it will be terminated and autostop continues.

`<unit>` can be one of:
- `m`: minutes (default if not specified)
- `h`: hours
- `d`: days
- `w`: weeks


Example:

```yaml
resources:
  autostop: true  # Stop after default idle minutes (5)
```

OR

```yaml
resources:
  autostop: 10  # Stop after 10 minutes
```

OR

```yaml
resources:
  autostop: 10h  # Stop after 10 hours
```

OR

```yaml
resources:
  autostop:
    idle_minutes: 10
    down: true  # Use autodown instead of autostop
```

OR

```yaml
resources:
  autostop:
    idle_minutes: 10
    wait_for: none  # Stop after 10 minutes, regardless of running jobs or SSH connections
```

OR

```yaml
resources:
  autostop:
    idle_minutes: 10
    hook: |
      cd my-code-base
      git add .
      git commit -m "Auto-commit before shutdown"
      git push
    hook_timeout: 300

```


### ``resources.accelerators``

Accelerator name and count per node (optional).

Use `sky gpus list` to view available accelerator configurations.

The following three ways are valid for specifying accelerators for a cluster:

- To specify a single type of accelerator:

  Format: `<name>:<count>` (or simply `<name>`, short for a count of 1).

  Example: `H100:4`

- To specify an ordered list of accelerators (try the accelerators in the specified order):

  Format: `[<name>:<count>, ...]`

  Example: `['L4:1', 'H100:1', 'A100:1']`

- To specify an unordered set of accelerators (optimize all specified accelerators together, and try accelerator with lowest cost first):

  Format: `{<name>:<count>, ...}`

  Example: `{'L4:1', 'H100:1', 'A100:1'}`

```yaml
resources:
  accelerators: V100:8
```

OR

```yaml
resources:
  accelerators:
    - A100:1
    - V100:1
```

OR

```yaml
resources:
  accelerators: {A100:1, V100:1}

```


### ``resources.accelerator_args``

Additional accelerator metadata (optional); only used for TPU node and TPU VM.

Example usage:

- To request a TPU VM:

```yaml
resources:
  accelerator_args:
    tpu_vm: true  # optional, default: True
```

- To request a TPU node:

```yaml
resources:
  accelerator_args:
    tpu_name: mytpu
    tpu_vm: false
```

By default, the value for `runtime_version` is decided based on which is requested and should work for either case. If passing in an incompatible version, GCP will throw an error during provisioning.

Example:

```yaml
resources:
  accelerator_args:
    # Default is "tpu-vm-base" for TPU VM and "2.12.0" for TPU node.
    runtime_version: tpu-vm-base
    # tpu_name: mytpu
    # tpu_vm: false  # True to use TPU VM (the default); False to use TPU node.


```


### ``resources.cpus``

Number of vCPUs per node (optional).

Format:

- `<count>`: exactly `<count>` vCPUs
- `<count>+`: at least `<count>` vCPUs

Example: `4+` means first try to find an instance type with >= 4 vCPUs. If not found, use the next cheapest instance with more than 4 vCPUs.

```yaml
resources:
  cpus: 4+
```

OR

```yaml
resources:
  cpus: 16

```


### ``resources.memory``

Memory specification per node (optional).

Format:

-  `<num>`: exactly `<num>` GB
-  `<num>+`: at least `<num>` GB
-  `<num><unit>`: memory with unit (e.g., `1024MB`, `64GB`)

Units supported (case-insensitive):
- KB (kilobytes, 2^10 bytes)
- MB (megabytes, 2^20 bytes)
- GB (gigabytes, 2^30 bytes) (default if not specified)
- TB (terabytes, 2^40 bytes)
- PB (petabytes, 2^50 bytes)

Example: `32+` means first try to find an instance type with >= 32 GiB. If not found, use the next cheapest instance with more than 32 GiB.

```yaml
resources:
  memory: 32+
```

OR

```yaml
resources:
  memory: 64GB
```


### ``resources.instance_type``

Instance type to use (optional).

If `accelerators` is specified, the corresponding instance type is automatically inferred.

```yaml
resources:
  instance_type: p3.8xlarge

```


### ``resources.use_spot``

Whether the cluster should use spot instances (optional).

If unspecified, defaults to `false` (on-demand instances).

```yaml
resources:
  use_spot: true

```


### ``resources.disk_size``

Integer disk size in GB to allocate for OS (mounted at `/`) OR specify units.

Increase this if you have a large working directory or tasks that write out large outputs.

Units supported (case-insensitive):

- KB (kilobytes, 2^10 bytes)
- MB (megabytes, 2^20 bytes)
- GB (gigabytes, 2^30 bytes)
- TB (terabytes, 2^40 bytes)
- PB (petabytes, 2^50 bytes)

> **WARNING**:
>
> The disk size will be rounded down (floored) to the nearest gigabyte. For example, ``1500MB`` or ``2000MB`` will be rounded to ``1GB``.

```yaml
resources:
  disk_size: 256
```

OR

```yaml
resources:
  disk_size: 256GB


```


### ``resources.disk_tier``
Disk tier to use for OS (optional).

Could be one of `'low'`, `'medium'`, `'high'`, `'ultra'` or `'best'` (default: `'medium'`).

If `'best'` is specified, use the best disk tier enabled.

Rough performance estimate:

- low: 1000 IOPS; read 90 MB/s; write 90 MB/s
- medium: 3000 IOPS; read 220 MB/s; write 220 MB/s
- high: 6000 IOPS; read 400 MB/s; write 400 MB/s
- ultra: 60000 IOPS;  read 4000 MB/s; write 3000 MB/s

Measured by `examples/perf/storage_rawperf.yaml`

```yaml
resources:
  disk_tier: medium
```

OR

```yaml
resources:
  disk_tier: best

```


### ``resources.network_tier``
Network tier to use (optional).

Could be one of `'standard'` or `'best'` (default: `'standard'`).

If `'best'` is specified, use the best network tier available on the specified infra. This currently supports:

- `infra: gcp`: Enable GPUDirect-TCPX for high-performance node-to-node GPU communication
- `infra: nebius`: Enable Infiniband for high-performance GPU communication across Nebius VMs. Currently only supported for H100:8 and H200:8 nodes.
- `infra: k8s/my-coreweave-cluster`: Enable InfiniBand for high-performance GPU communication across pods on CoreWeave CKS clusters.
- `infra: k8s/my-nebius-cluster`: Enable InfiniBand for high-performance GPU communication across pods on Nebius managed Kubernetes.
- `infra: k8s/my-gke-cluster`: Enable GPUDirect-TCPX/TCPXO/RDMA for high-performance GPU communication across pods on Google Kubernetes Engine (GKE).

```yaml
resources:
  network_tier: best

```


### ``resources.ports``

Ports to expose (optional).

All ports specified here will be exposed to the public Internet. Under the hood, a firewall rule / inbound rule is automatically added to allow inbound traffic to these ports.

Applies to all VMs of a cluster created with this field set.

Currently only TCP protocol is supported.

Ports Lifecycle:

A cluster's ports will be updated whenever `sky launch` is executed. When launching an existing cluster, any new ports specified will be opened for the cluster, and the firewall rules for old ports will never be removed until the cluster is terminated.

Could be an integer, a range, or a list of integers and ranges:

- To specify a single port: `8081`
- To specify a port range: `10052-10100`
- To specify multiple ports / port ranges:

```yaml
resources:
ports:
  - 8080
  - 10022-10040
```

OR

```yaml
resources:
  ports: 8081
```

OR

```yaml
resources:
  ports: 10052-10100
```

OR

```yaml
resources:
  ports:
    - 8080
    - 10022-10040

```


### ``resources.image_id``
Custom image id (optional, advanced).

The image id used to boot the instances. Only supported for AWS, GCP, OCI, IBM and Verda. IBM and Verda only support non-docker images.

If not specified, SkyPilot will use the default debian-based image suitable for machine learning tasks.

**Docker support**

You can specify docker image to use by setting the image_id to `docker:<image name>` for Azure, AWS, GCP, and RunPod. For example,

```yaml
resources:
  image_id: docker:ubuntu:latest
```

Currently, only debian and ubuntu images are supported.

If you want to use a docker image in a private registry, you can specify your username, password, and registry server as task environment variable. For details, please refer to the `envs` section below.

**AWS**

To find AWS AMI ids: https://leaherb.com/how-to-find-an-aws-marketplace-ami-image-id

You can also change the default OS version by choosing from the following image tags provided by SkyPilot:

```yaml
resources:
  image_id: skypilot:gpu-ubuntu-2004
  image_id: skypilot:k80-ubuntu-2004
  image_id: skypilot:gpu-ubuntu-1804
  image_id: skypilot:k80-ubuntu-1804
```

It is also possible to specify a per-region image id (failover will only go through the regions specified as keys; useful when you have the custom images in multiple regions):

```yaml
resources:
  image_id:
    us-east-1: ami-0729d913a335efca7
    us-west-2: ami-050814f384259894c
```

**GCP**

To find GCP images: https://cloud.google.com/compute/docs/images

```yaml
resources:
  image_id: projects/deeplearning-platform-release/global/images/common-cpu-v20230615-debian-11-py310
```

Or machine image: https://cloud.google.com/compute/docs/machine-images

```yaml
resources:
  image_id: projects/my-project/global/machineImages/my-machine-image
```

**Azure**

To find Azure images: https://docs.microsoft.com/en-us/azure/virtual-machines/linux/cli-ps-findimage

```yaml
resources:
  image_id: microsoft-dsvm:ubuntu-2004:2004:21.11.04
```

**OCI**

To find OCI images: https://docs.oracle.com/en-us/iaas/images

You can choose the image with OS version from the following image tags provided by SkyPilot:

```yaml
resources:
  image_id: skypilot:gpu-ubuntu-2204
  image_id: skypilot:gpu-ubuntu-2004
  image_id: skypilot:gpu-oraclelinux9
  image_id: skypilot:gpu-oraclelinux8
  image_id: skypilot:cpu-ubuntu-2204
  image_id: skypilot:cpu-ubuntu-2004
  image_id: skypilot:cpu-oraclelinux9
  image_id: skypilot:cpu-oraclelinux8
```

It is also possible to specify your custom image's OCID with OS type, for example:

```yaml
resources:
  image_id: ocid1.image.oc1.us-sanjose-1.aaaaaaaaywwfvy67wwe7f24juvjwhyjn3u7g7s3wzkhduxcbewzaeki2nt5q:oraclelinux
  image_id: ocid1.image.oc1.us-sanjose-1.aaaaaaaa5tnuiqevhoyfnaa5pqeiwjv6w5vf6w4q2hpj3atyvu3yd6rhlhyq:ubuntu
```

**IBM**

Create a private VPC image and paste its ID in the following format:

```yaml
resources:
  image_id: <unique_image_id>
```

To create an image manually:
https://cloud.ibm.com/docs/vpc?topic=vpc-creating-and-using-an-image-from-volume.

To use an official VPC image creation tool:
https://www.ibm.com/cloud/blog/use-ibm-packer-plugin-to-create-custom-images-on-ibm-cloud-vpc-infrastructure

To use a more limited but easier to manage tool:
https://github.com/IBM/vpc-img-inst

```yaml
resources:
  image_id: ami-0868a20f5a3bf9702  # IBM example
  # image_id: projects/deeplearning-platform-release/global/images/common-cpu-v20230615-debian-11-py310  # GCP example
  # image_id: docker:pytorch/pytorch:1.13.1-cuda11.6-cudnn8-runtime # Docker example
```

OR

```yaml
resources:
  image_id:
    us-east-1: ami-123
    us-west-2: ami-456

```

**RunPod**

RunPod natively supports Docker images. You can specify any Docker image:

```yaml
resources:
  image_id: docker:ubuntu:22.04
  # Or use a specific registry
  image_id: docker:nvcr.io/nvidia/pytorch:24.10-py3
```

For multi-region deployments, you can specify different images per region:

```yaml
resources:
  image_id:
    US: docker:us-registry.io/myapp:latest
    CA: docker:ca-registry.io/myapp:latest
    CZ: docker:eu-registry.io/myapp:latest

```


### ``resources.labels``
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

```yaml
resources:
  labels:
    project: my-project
    department: research

```


### ``resources.any_of``
Candidate resources (optional).

If specified, SkyPilot will only use these candidate resources to launch the cluster.

The fields specified outside of `any_of` will be used as the default values for all candidate resources, and any duplicate fields specified inside `any_of` will override the default values.

`any_of` means that SkyPilot will try to find a resource that matches any of the candidate resources, i.e. the failover order will be decided by the optimizer.

Example:

```yaml
resources:
  accelerators: H100
  any_of:
    - infra: aws/us-west-2
    - infra: gcp/us-central1
```


### ``resources.ordered``
Ordered candidate resources (optional).

If specified, SkyPilot will failover through the candidate resources with the specified order.

The fields specified outside of `ordered` will be used as the default values for all candidate resources, and any duplicate fields specified inside `ordered` will override the default values.

`ordered` means that SkyPilot will failover through the candidate resources with the specified order.

Example:

```yaml
resources:
  ordered:
    - infra: aws/us-east-1
    - infra: aws/us-west-2
```


### ``resources.job_recovery``
The recovery strategy for managed jobs (optional).

We can specify the strategy for which region to recover the job to when it fails. Possible values are `FAILOVER` and `EAGER_NEXT_REGION`.

If `FAILOVER` is specified, the job will be restarted in the same region if the node fails, and go to the next region if no available resources are found in the same region.

If `EAGER_NEXT_REGION` is specified, the job will go to the next region directly if the node fails. This is useful for spot instances, as in practice, preemptions in a region usually indicate a shortage of resources in that region.

Default: `EAGER_NEXT_REGION`

Example:

```yaml
resources:
  job_recovery:
    strategy: FAILOVER
```

OR

```yaml
resources:
  job_recovery:
    strategy: EAGER_NEXT_REGION
```

We can also specify the maximum number of times to restart the job on user code errors (non-zero exit codes).

```yaml
resources:
  job_recovery:
    max_restarts_on_errors: 3
```

We can also specify the exit codes that should always trigger recovery, regardless of the `max_restarts_on_errors` limit. This is useful when certain exit codes indicate transient errors that should always be retried (e.g., NCCL timeouts, specific GPU driver issues).

We can specify multiple exit codes:

```yaml
resources:
  job_recovery:
    # Always recover on these exit codes
    recover_on_exit_codes: [33, 34]
```

Or a single exit code:

```yaml
resources:
  job_recovery:
    # Always recover on these exit codes
    recover_on_exit_codes: 33
```

Available fields:

- `strategy`: The recovery strategy to use (`FAILOVER` or `EAGER_NEXT_REGION`)
- `max_restarts_on_errors`: Maximum number of times to restart the job on user code errors (non-zero exit codes)
- `recover_on_exit_codes`: Exit code(s) (0-255) that should always trigger recovery. Can be a single integer (e.g., `33`) or a list (e.g., `[33, 34]`). Restarts triggered by these exit codes do not count towards the `max_restarts_on_errors` limit. Useful for specific transient errors like NCCL timeouts.



### ``envs``

Environment variables (optional).

These values can be accessed in the `file_mounts`, `setup`, and `run` sections below.

Values set here can be overridden by a CLI flag: `sky launch/exec --env ENV=val` (if `ENV` is present).


Example of using envs:

```yaml
envs:
  MY_BUCKET: skypilot-data
  MODEL_SIZE: 13b
  MY_LOCAL_PATH: tmp-workdir
```

  For costumized non-root docker image in RunPod, you need to set `SKYPILOT_RUNPOD_DOCKER_USERNAME` to specify the login username for the docker image. See docker-containers-as-runtime-environments for more.

  If you want to use a docker image as runtime environment in a private registry, you can specify your username, password, and registry server as task environment variable.  For example:

```yaml
envs:
  SKYPILOT_DOCKER_USERNAME: <username>
  SKYPILOT_DOCKER_PASSWORD: <password>
  SKYPILOT_DOCKER_SERVER: <registry server>
```

  SkyPilot will execute `docker login --username <username> --password <password> <registry server>` before pulling the docker image. For `docker login`, see https://docs.docker.com/engine/reference/commandline/login/

  You could also specify any of them through the CLI flag if you don't want to store them in your yaml file or if you want to generate them for constantly changing password. For example:

```yaml
sky launch --env SKYPILOT_DOCKER_PASSWORD=$(aws ecr get-login-password --region us-east-1).
```

  For more information about docker support in SkyPilot, please refer to Using private docker registries.

  You can also use secrets to set the authentication above.


### ``secrets``

Secrets (optional).

Secrets are similar to envs above but can only be used in the `setup` and `run`, and will be redacted in the entrypoint/YAML in the dashboard.

Values set here can be overridden by a CLI flag: `sky launch/exec --secret SECRET=val` (if `SECRET` is present).

Example:

```yaml
secrets:
  HF_TOKEN: my-huggingface-token
  WANDB_API_KEY: my-wandb-api-key
```


### ``volumes``

SkyPilot supports managing persistent and ephemeral volumes for tasks or jobs on Kubernetes clusters. Refer to volumes on Kubernetes for more details.

Example:

```yaml
volumes:
  # Persistent volume
  /mnt/data: volume-name
  # Ephemeral volume
  /mnt/cache:
    size: 100Gi

```


### ``file_mounts``

File mounts configuration.

Example:

```yaml
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
    store: s3  # Could be either 's3', 'gcs', 'azure', 'r2', 'vastdata', 'oci', or 'ibm'; default: None. Optional.
    persistent: True  # Defaults to True; can be set to false to delete bucket after cluster is downed. Optional.
    mode: MOUNT  # MOUNT or COPY or MOUNT_CACHED. Defaults to MOUNT. Optional.

  # Copies a cloud object store URI to the cluster. Can be private buckets.
  /datasets-s3: s3://my-awesome-dataset

  # Demoing env var usage.
  /checkpoint/${MODEL_SIZE}: ~/${MY_LOCAL_PATH}
  /mydir:
    name: ${MY_BUCKET}  # Name of the bucket.
    mode: MOUNT
```

OR

```yaml
file_mounts:
  /remote/data: ./local_data  # Local to remote
  /remote/output: s3://my-bucket/outputs  # Cloud storage
  /remote/models:
    name: my-models-bucket
    source: ~/local_models
    store: gcs
    mode: MOUNT
```


### ``setup``

Setup script (optional) to execute on every `sky launch`.

This is executed before the `run` commands.

Example:

To specify a single command:

```yaml
setup: pip install -r requirements.txt
```

The `|` separator indicates a multiline string.

```yaml
setup: |
  echo "Begin setup."
  pip install -r requirements.txt
  echo "Setup complete."
```

OR

```yaml
setup: |
  conda create -n myenv python=3.9 -y
  conda activate myenv
  pip install torch torchvision
```


### ``run``

Main program (optional, but recommended) to run on every node of the cluster.

Example:

```yaml
run: |
  echo "Beginning task."
  python train.py

  # Demoing env var usage.
  echo Env var MODEL_SIZE has value: ${MODEL_SIZE}
```

OR

```yaml
run: |
  conda activate myenv
  python my_script.py --data-dir /remote/data --output-dir /remote/output

```


### ``config``

Advanced configuration options to apply to the task.

Example:

```yaml
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
```


# SkyServe Service

To define a YAML for use for services, use previously mentioned fields to describe each replica, then add a service section to describe the entire service.

Syntax

```yaml
service:
  readiness_probe:
    path: /v1/models
    post_data: {'model_name': 'model'}
    initial_delay_seconds: 1200
    timeout_seconds: 15

  readiness_probe: /v1/models

  replica_policy:
    min_replicas: 1
    max_replicas: 3
    target_qps_per_replica: 5
    upscale_delay_seconds: 300
    downscale_delay_seconds: 1200

  replicas: 2

resources:
  ports: 8080

```

## Fields


### ``service.readiness_probe``

Readiness probe configuration (required).

Used by SkyServe to check if your service replicas are ready for accepting traffic.

If the readiness probe returns a 200, SkyServe will start routing traffic to that replica.

Can be defined as a path string (for GET requests with defaults) or a detailed dictionary.

```yaml
service:
  readiness_probe: /v1/models
```

OR

```yaml
service:
  readiness_probe:
    path: /v1/models
    post_data: '{"model_name": "my_model"}'
    initial_delay_seconds: 600
    timeout_seconds: 10

```


### ``service.readiness_probe.path``

Endpoint path for readiness checks (required).

Path to probe. SkyServe sends periodic requests to this path after the initial delay.

```yaml
service:
  readiness_probe:
    path: /v1/models

```


### ``service.readiness_probe.post_data``

POST request payload (optional).

If this is specified, the readiness probe will use POST instead of GET, and the post data will be sent as the request body.

```yaml
service:
  readiness_probe:
    path: /v1/models
    post_data: '{"model_name": "my_model"}'
```


### ``service.readiness_probe.initial_delay_seconds``

Grace period before initiating health checks (default: 1200).

Initial delay in seconds. Any readiness probe failures during this period will be ignored.

This is highly related to your service, so it is recommended to set this value based on your service's startup time.


```yaml
service:
  readiness_probe:
    initial_delay_seconds: 600
```


### ``service.readiness_probe.timeout_seconds``

Maximum wait time per probe request (default: 15).

The Timeout in seconds for a readiness probe request.

If the readiness probe takes longer than this time to respond, the probe will be considered as failed.

This is useful when your service is slow to respond to readiness probe requests.

Note, having a too high timeout will delay the detection of a real failure of your service replica.

```yaml
  service:
    readiness_probe:
      timeout_seconds: 10

```


### ``service.replica_policy``

Autoscaling configuration for service replicas (one of replica_policy or replicas is required).

Describes how SkyServe autoscales your service based on the QPS (queries per second) of your service.

```yaml
  service:
    replica_policy:
      min_replicas: 1
      max_replicas: 5
      target_qps_per_replica: 10
```


### ``service.replica_policy.min_replicas``

Minimum number of active replicas (required).

Service never scales below this count.

```yaml
service:
  replica_policy:
    min_replicas: 1

```


### ``service.replica_policy.max_replicas``

Maximum allowed replicas (optional).

If not specified, SkyServe will use a fixed number of replicas (the same as min_replicas) and ignore any QPS threshold specified below.

```yaml
service:
  replica_policy:
    max_replicas: 3

```


### ``service.replica_policy.target_qps_per_replica``

Target queries per second per replica (optional).

SkyServe will scale your service so that, ultimately, each replica manages approximately `target_qps_per_replica` queries per second.

**Autoscaling will only be enabled if this value is specified.**

```yaml
service:
  replica_policy:
    target_qps_per_replica: 5

```


### ``service.replica_policy.upscale_delay_seconds``

Stabilization period before adding replicas (default: 300).

Upscale delay in seconds. To avoid aggressive autoscaling, SkyServe will only upscale your service if the QPS of your service is higher than the target QPS for a period of time.

```yaml
service:
  replica_policy:
    upscale_delay_seconds: 300

```


### ``service.replica_policy.downscale_delay_seconds``

Cooldown period before removing replicas (default: 1200).

Downscale delay in seconds. To avoid aggressive autoscaling, SkyServe will only downscale your service if the QPS of your service is lower than the target QPS for a period of time.

```yaml
service:
  replica_policy:
    downscale_delay_seconds: 1200

```


### ``service.replicas``

Fixed replica count alternative to autoscaling.

Simplified version of replica policy that uses a fixed number of replicas.

```yaml
service:
  replicas: 2

```


### ``resources.ports``

Required exposed port for service traffic.

Port to run your service on each replica.

```yaml
resources:
  ports: 8080
```


# Job Pools

To define a YAML for use with job pools, use previously mentioned fields to describe each worker, then add a pool section to configure the pool's scaling behavior.

Syntax

```yaml
pool:
  workers: 3

pool:
  min_workers: 1
  max_workers: 10
  queue_length_threshold: 5
  upscale_delay_seconds: 300
  downscale_delay_seconds: 1200

```

## Fields


### ``pool.workers``

Number of workers in the pool.

If `min_workers` and `max_workers` are not specified, the pool maintains a fixed number of workers with no autoscaling. If autoscaling is enabled (`min_workers`/`max_workers` are set), this serves as the initial number of workers.

```yaml
pool:
  workers: 3

```


### ``pool.min_workers``

Minimum number of workers when autoscaling is enabled (required with `max_workers`).

The pool never scales below this count. Setting to `0` enables **scale-to-zero**: the pool terminates all workers when idle, and provisions workers automatically when new jobs are submitted.

```yaml
pool:
  min_workers: 1
  max_workers: 10

```


### ``pool.max_workers``

Maximum number of workers when autoscaling is enabled (required with `min_workers`).

The pool never scales above this count. Must be greater than or equal to `min_workers`.

```yaml
pool:
  min_workers: 1
  max_workers: 10

```


### ``pool.queue_length_threshold``

Number of pending jobs that triggers upscaling (default: 1).

When the number of pending jobs exceeds this threshold, the pool scales up. Requires `max_workers` to be set.

```yaml
pool:
  min_workers: 1
  max_workers: 10
  queue_length_threshold: 5

```


### ``pool.upscale_delay_seconds``

Delay in seconds between upscaling decisions (default: 300).

Controls how frequently the pool evaluates whether to add workers.

```yaml
pool:
  min_workers: 1
  max_workers: 10
  upscale_delay_seconds: 60

```


### ``pool.downscale_delay_seconds``

Delay in seconds between downscaling decisions (default: 1200).

Controls how frequently the pool evaluates whether to remove workers.

```yaml
pool:
  min_workers: 1
  max_workers: 10
  downscale_delay_seconds: 600
```

.. _config-yaml:

Advanced Configuration
======================

You can pass **optional configuration** to SkyPilot in the ``~/.sky/config.yaml`` file (for :ref:`local API server <sky-api-server-local>`) or on your SkyPilot API server dashboard (for :ref:`remote API server <sky-api-server-remote>`) at ``http://<api-server-url>/dashboard/config``.

Configuration sources and overrides
-----------------------------------

SkyPilot allows you to set configuration globally in ``~/.sky/config.yaml`` (for :ref:`local API server <sky-api-server-local>`) or on the API server dashboard (for :ref:`remote API server <sky-api-server-remote>`) at ``http://<api-server-url>/dashboard/config``, in your project, or for specific jobs, providing flexibility in how you manage your configurations.

For example, you can have a :ref:`user configuration<config-client-user-config>` to apply globally to all projects, a :ref:`project configuration<config-client-project-config>` storing default values for all jobs in a project, and :ref:`Task YAML overrides<config-client-cli-flag>` for specific jobs.

Refer to :ref:`config-sources-and-overrides` for more details.

.. _config-yaml-syntax:

Syntax
------

For details about SkyServe controller and its customization, see :ref:`customizing-sky-serve-controller-resources`.

Below is the configuration syntax and some example values. See detailed explanations under each field.

.. parsed-literal::

  :ref:`api_server <config-yaml-api-server>`:
    :ref:`endpoint <config-yaml-api-server-endpoint>`: \http://xx.xx.xx.xx:8000
    :ref:`service_account_token <config-yaml-api-server-service-account-token>`: sky_xxx
    :ref:`requests_retention_hours <config-yaml-api-server-requests-gc-retention-hours>`: 24
    :ref:`cluster_event_retention_hours <config-yaml-api-server-cluster-event-retention-hours>`: 24
    :ref:`cluster_debug_event_retention_hours <config-yaml-api-server-cluster-debug-event-retention-hours>`: 720

  :ref:`allowed_clouds <config-yaml-allowed-clouds>`:
    - aws
    - gcp
    - kubernetes

  :ref:`jobs <config-yaml-jobs>`:
    :ref:`bucket <config-yaml-jobs-bucket>`: s3://my-bucket/
    :ref:`force_disable_cloud_bucket <config-yaml-jobs-force-disable-cloud-bucket>`: false
    controller:
      :ref:`resources <config-yaml-jobs-controller-resources>`:  # same spec as 'resources' in a task YAML
        infra: gcp/us-central1
        cpus: 4+  # number of vCPUs, max concurrent spot jobs = 2 * cpus
        disk_size: 100
      :ref:`autostop <config-yaml-jobs-controller-autostop>`:
        idle_minutes: 10
        down: false  # use with caution!
      :ref:`controller_logs_gc_retention_hours <config-yaml-jobs-controller-controller-logs-gc-retention-hours>`: 24 * 7
      :ref:`task_logs_gc_retention_hours <config-yaml-jobs-controller-task-logs-gc-retention-hours>`: 24 * 7

  :ref:`docker <config-yaml-docker>`:
    :ref:`run_options <config-yaml-docker-run-options>`:
      - -v /var/run/docker.sock:/var/run/docker.sock
      - --shm-size=2g

  :ref:`nvidia_gpus <config-yaml-nvidia-gpus>`:
    :ref:`disable_ecc <config-yaml-nvidia-gpus-disable-ecc>`: false

  :ref:`admin_policy <config-yaml-admin-policy>`: my_package.SkyPilotPolicyV1

  :ref:`provision <config-yaml-provision>`:
    :ref:`ssh_timeout <config-yaml-provision-ssh-timeout>`: 10
    :ref:`install_conda <config-yaml-provision-install-conda>`: false

  :ref:`kubernetes <config-yaml-kubernetes>`:
    :ref:`ports <config-yaml-kubernetes-ports>`: loadbalancer
    :ref:`remote_identity <config-yaml-kubernetes-remote-identity>`: my-k8s-service-account
    :ref:`allowed_contexts <config-yaml-kubernetes-allowed-contexts>`:
      - context1
      - context2
    :ref:`custom_metadata <config-yaml-kubernetes-custom-metadata>`:
      labels:
        mylabel: myvalue
      annotations:
        myannotation: myvalue
    :ref:`provision_timeout <config-yaml-kubernetes-provision-timeout>`: 10
    :ref:`autoscaler <config-yaml-kubernetes-autoscaler>`: gke
    :ref:`pod_config <config-yaml-kubernetes-pod-config>`:
      metadata:
        labels:
          my-label: my-value
      spec:
        runtimeClassName: nvidia
    :ref:`kueue <config-yaml-kubernetes-kueue>`:
      :ref:`local_queue_name <config-yaml-kubernetes-kueue-local-queue-name>`: skypilot-local-queue
    :ref:`dws <config-yaml-kubernetes-dws>`:
      enabled: true
      max_run_duration: 10m
    :ref:`post_provision_runcmd <config-yaml-kubernetes-post-provision-runcmd>`:
      - echo "hello world!"
    :ref:`context_configs <config-yaml-kubernetes-context-configs>`:
      context1:
        pod_config:
          metadata:
            labels:
              my-label: my-value
      context2:
        remote_identity: my-k8s-service-account

  :ref:`ssh <config-yaml-ssh>`:
    # See :ref:`kubernetes.pod_config <config-yaml-kubernetes-pod-config>` for more details.
    pod_config: ...
    # See :ref:`kubernetes.provision_timeout <config-yaml-kubernetes-provision-timeout>` for more details.
    provision_timeout: ...
    # Specifying above fields but for a specific context.
    context_configs:
      node-pool-1:
        pod_config:
          metadata:
            labels:
              my-label: my-value
      node-pool-2:
        provision_timeout: 3600
    :ref:`allowed_node_pools <config-yaml-ssh-allowed-node-pools>`:
      - node-pool-1
      - node-pool-2

  :ref:`slurm <config-yaml-slurm>`:
    :ref:`allowed_clusters <config-yaml-slurm-allowed-clusters>`:
      - mycluster1
      - mycluster2
    :ref:`provision_timeout <config-yaml-slurm-provision-timeout>`: 120

  :ref:`aws <config-yaml-aws>`:
    :ref:`labels <config-yaml-aws-labels>`:
      map-migrated: my-value
      Owner: user-unique-name
    :ref:`vpc_names <config-yaml-aws-vpc-names>`:
      - skypilot-vpc-1
      - skypilot-vpc-2
    :ref:`use_internal_ips <config-yaml-aws-use-internal-ips>`: true
    :ref:`use_ssm <config-yaml-aws-use-ssm>`: true
    :ref:`ssh_proxy_command <config-yaml-aws-ssh-proxy-command>`: ssh -W %h:%p user@host
    :ref:`security_group_name <config-yaml-aws-security-group-name>`: my-security-group
    :ref:`disk_encrypted <config-yaml-aws-disk-encrypted>`: false
    :ref:`ssh_user <config-yaml-aws-ssh-user>`: ubuntu
    :ref:`prioritize_reservations <config-yaml-aws-prioritize-reservations>`: false
    :ref:`specific_reservations <config-yaml-aws-specific-reservations>`:
      - cr-a1234567
    :ref:`remote_identity <config-yaml-aws-remote-identity>`: LOCAL_CREDENTIALS
    :ref:`post_provision_runcmd <config-yaml-aws-post-provision-runcmd>`:
      - echo "hello world!"
    :ref:`capabilities <config-yaml-aws-capabilities>`:
      - storage

  :ref:`gcp <config-yaml-gcp>`:
    :ref:`labels <config-yaml-gcp-labels>`:
      Owner: user-unique-name
      my-label: my-value
    :ref:`vpc_name <config-yaml-gcp-vpc-name>`: skypilot-vpc
    :ref:`use_internal_ips <config-yaml-gcp-use-internal-ips>`: true
    :ref:`force_enable_external_ips <config-yaml-gcp-force-enable-external-ips>`: true
    :ref:`ssh_proxy_command <config-yaml-gcp-ssh-proxy-command>`: ssh -W %h:%p user@host
    :ref:`prioritize_reservations <config-yaml-gcp-prioritize-reservations>`: false
    :ref:`specific_reservations <config-yaml-gcp-specific-reservations>`:
      - projects/my-project/reservations/my-reservation1
    :ref:`managed_instance_group <config-yaml-gcp-managed-instance-group>`:
      run_duration: 3600
      provision_timeout: 900
    :ref:`remote_identity <config-yaml-gcp-remote-identity>`: LOCAL_CREDENTIALS
    :ref:`enable_gvnic <config-yaml-gcp-enable-gvnic>`: false
    :ref:`enable_gpu_direct <config-yaml-gcp-enable-gpu-direct>`: false
    :ref:`placement_policy <config-yaml-gcp-placement-policy>`: compact
    :ref:`capabilities <config-yaml-gcp-capabilities>`:
      - storage

  :ref:`azure <config-yaml-azure>`:
    :ref:`resource_group_vm <config-yaml-azure-resource-group-vm>`: user-resource-group-name
    :ref:`storage_account <config-yaml-azure-storage-account>`: user-storage-account-name

  :ref:`oci <config-yaml-oci>`:
    region_configs:
      :ref:`default <config-yaml-oci>`:
        oci_config_profile: SKY_PROVISION_PROFILE
        compartment_ocid: ocid1.compartment.oc1..aaaaaaaahr7aicqtodxmcfor6pbqn3hvsngpftozyxzqw36gj4kh3w3kkj4q
        image_tag_general: skypilot:cpu-oraclelinux8
        image_tag_gpu: skypilot:gpu-oraclelinux8
      :ref:`ap-seoul-1 <config-yaml-oci>`:
        vcn_ocid: ocid1.vcn.oc1.ap-seoul-1.amaaaaaaak7gbriarkfs2ssus5mh347ktmi3xa72tadajep6asio3ubqgarq
        vcn_subnet: ocid1.subnet.oc1.ap-seoul-1.aaaaaaaa5c6wndifsij6yfyfehmi3tazn6mvhhiewqmajzcrlryurnl7nuja
      :ref:`us-ashburn-1 <config-yaml-oci>`:
        vcn_ocid: ocid1.vcn.oc1.ap-seoul-1.amaaaaaaak7gbriarkfs2ssus5mh347ktmi3xa72tadajep6asio3ubqgarq
        vcn_subnet: ocid1.subnet.oc1.iad.aaaaaaaafbj7i3aqc4ofjaapa5edakde6g4ea2yaslcsay32cthp7qo55pxa

  :ref:`nebius <config-yaml-nebius>`:
    region_configs:
      :ref:`eu-north1 <config-yaml-nebius>`:
        project_id: project-e00xxxxxxxxxxx
        fabric: fabric-3
        filesystems:
        - filesystem_id: computefilesystem-e00xwrry01ysvykbhf
          mount_path: /mnt/fsnew
          attach_mode: READ_WRITE
      :ref:`eu-west1 <config-yaml-nebius>`:
        project_id: project-e01xxxxxxxxxxx
        fabric: fabric-5
    :ref:`use_internal_ips <config-yaml-nebius-use-internal-ips>`: true
    :ref:`use_static_ip_address <config-yaml-nebius-use-static-ip-address>`: true
    :ref:`ssh_proxy_command <config-yaml-nebius-ssh-proxy-command>`: ssh -W %h:%p user@host
    :ref:`tenant_id <config-yaml-nebius-tenant-id>`: tenant-1234567890
    :ref:`domain <config-yaml-nebius-domain>`: api.nebius.cloud:443

  :ref:`vast <config-yaml-vast>`:
    :ref:`datacenter_only <config-yaml-vast-datacenter-only>`: true
    :ref:`create_instance_kwargs <config-yaml-vast-create-instance-kwargs>`:
      template_hash: f0e124f0e98bfbc2ecb05dc713009ee7
      env: "-e YOUR_CUSTOM=YOUR_VAL"

  :ref:`rbac <config-yaml-rbac>`:
    :ref:`default_role <config-yaml-rbac-default-role>`: admin

  :ref:`db <config-yaml-db>`: postgresql://postgres@localhost/skypilot

  :ref:`logs <config-yaml-logs>`:
    :ref:`store <config-yaml-logs-store>`: gcp
    gcp:
      project_id: my-project-id

  :ref:`daemons <config-yaml-daemons>`:
    skypilot-status-refresh-daemon:
      log_level: DEBUG

Fields
----------


.. _config-yaml-api-server:

``api_server``
~~~~~~~~~~~~~~~~~~~

Configure the SkyPilot API server.

.. _config-yaml-api-server-endpoint:

``api_server.endpoint``
~~~~~~~~~~~~~~~~~~~~~~~

Endpoint of the SkyPilot API server (optional).

This is used to connect to the SkyPilot API server.

Default: ``null`` (use the local endpoint, which will be started by SkyPilot automatically).

Example:

.. code-block:: yaml

  api_server:
    endpoint: http://xx.xx.xx.xx:8000

.. _config-yaml-api-server-service-account-token:

``api_server.service_account_token``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Service account token for the SkyPilot API server (optional). For more details, see :ref:`service-accounts`.

.. _config-yaml-api-server-requests-gc-retention-hours:

``api_server.requests_retention_hours``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retention period for finished requests in hours (optional). Set to a negative value to disable requests GC.

Requests GC will remove request entries in `sky api status`, i.e., the logs and status of the requests. All the launched resources (clusters/jobs) will still be correctly running.

Default: ``24.0`` (1 day).

Example:

.. code-block:: yaml

  api_server:
    requests_retention_hours: -1 # Disable requests GC

.. _config-yaml-api-server-cluster-event-retention-hours:

``api_server.cluster_event_retention_hours``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retention period for cluster events in hours (optional). Set to a negative value to disable cluster event GC.

Cluster event GC will remove cluster event entries in `sky status -v`, i.e., the logs and status of the cluster events.

Default: ``24.0`` (1 day).

Example:

.. code-block:: yaml

  api_server:
    cluster_event_retention_hours: -1 # Disable all cluster event GC

.. _config-yaml-api-server-cluster-debug-event-retention-hours:

``api_server.cluster_debug_event_retention_hours``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retention period for cluster events in hours (optional). Set to a negative value to disable cluster event GC.

Cluster event GC will remove debug cluster event entries in `sky status -v`, i.e., the logs and status of the cluster events.

Default: ``720.0`` (30 days).

Example:

.. code-block:: yaml

  api_server:
    cluster_debug_event_retention_hours: -1 # Disable all cluster event GC

.. _config-yaml-jobs:

``jobs``
~~~~~~~~

Custom managed jobs controller resources (optional).

These take effects only when a managed jobs controller does not already exist.

For more information about managed jobs, see :ref:`managed-jobs`.


.. _config-yaml-jobs-bucket:

``jobs.bucket``
~~~~~~~~~~~~~~~

Bucket to store managed jobs mount files and tmp files. Bucket must already exist.

Optional. If not set, SkyPilot will create a new bucket for each managed job launch.

Supported bucket types:

.. code-block:: yaml

  jobs:
    bucket: s3://my-bucket/
    # bucket: gs://my-bucket/
    # bucket: https://<azure_storage_account>.blob.core.windows.net/<container>
    # bucket: r2://my-bucket/
    # bucket: cos://<region>/<bucket>

.. _config-yaml-jobs-force-disable-cloud-bucket:

``jobs.force_disable_cloud_bucket``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Force-disable using a cloud bucket for storing intermediate job files (optional).

If set to ``true``, SkyPilot will not use cloud object storage as an intermediate storage for files for managed jobs, even if cloud storage is available.

Files will be uploaded directly to the jobs controller and downloaded on to the job nodes from there (two-hop trasnfer). Useful in environments where use of cloud buckets must be avoided.

Default: ``false``.

Example:

.. code-block:: yaml

  jobs:
    force_disable_cloud_bucket: true

.. _config-yaml-jobs-controller:
.. _config-yaml-jobs-controller-consolidation-mode:

``jobs.controller.consolidation_mode``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Enable :ref:`consolidation mode <jobs-consolidation-mode>`, which will run the jobs controller within the remote API server, rather than in a separate sky cluster. Don't enable unless you are using a remotely-deployed API server.

Default: ``false``.

Example:

.. code-block:: yaml

  jobs:
    controller:
      consolidation_mode: true
      # any specified resources will be ignored

.. _config-yaml-jobs-controller-resources:

``jobs.controller.resources``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configure resources for the managed jobs controller.

For more details about tuning the jobs controller resources, see :ref:`jobs-controller-sizing`.

Example:

.. code-block:: yaml

  jobs:
    controller:
      resources:  # same spec as 'resources' in a task YAML
        # optionally set specific cloud/region
        infra: gcp/us-central1
        # default resources:
        cpus: 4+
        memory: 8x
        disk_size: 50

.. _config-yaml-jobs-controller-autostop:

``jobs.controller.autostop``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configure :ref:`autostop <auto-stop>` for the managed jobs controller.

By default, the jobs controller is autostopped after 10 minutes, except on Kubernetes and RunPod, where it is not supported. The controller will be automatically restarted when a new job is launched.

If you want the controller to automatically terminate instead of autostopping, set ``down: true``. Use this with caution: ``down: true`` can leak clusters if SkyPilot crashes and all job logs will be lost when the controller is terminated.

Example:

.. code-block:: yaml

  jobs:
    controller:
      # Disable autostop.
      autostop: false

.. code-block:: yaml

  jobs:
    controller:
      # Enable autostop with custom config.
      autostop:
        # Default values:
        idle_minutes: 10  # Set time to idle autostop/autodown.
        down: false  # Terminate instead of stopping. Caution: setting this to true will cause logs to be lost and could lead to resource leaks if SkyPilot crashes.


.. _config-yaml-jobs-controller-controller-logs-gc-retention-hours:

``jobs.controller.controller_logs_gc_retention_hours``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retention period for controller logs in hours (optional). Set to a negative
value to disable controller logs garbage collection.

Controller logs GC will automatically delete old controller logs from the
job controller to reclaim disk space.

Default: ``168`` (7 days).

Example:

.. code-block:: yaml

  jobs:
    controller:
      # Keep controller logs for 24 hours (1 day)
      controller_logs_gc_retention_hours: 24

.. code-block:: yaml

  jobs:
    controller:
      # Disable controller logs GC (keep all logs indefinitely)
      controller_logs_gc_retention_hours: -1


.. _config-yaml-jobs-controller-task-logs-gc-retention-hours:

``jobs.controller.task_logs_gc_retention_hours``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retention period for task logs in hours (optional). Set to a negative value
to disable task logs garbage collection.

Task logs GC will automatically delete old task logs from the job controller
to reclaim disk space.

Default: ``168`` (7 days).

Example:

.. code-block:: yaml

  jobs:
    controller:
      # Keep task logs for 48 hours (2 days)
      task_logs_gc_retention_hours: 48

.. code-block:: yaml

  jobs:
    controller:
      # Disable task logs GC (keep all logs indefinitely)
      task_logs_gc_retention_hours: -1


.. _config-yaml-allowed-clouds:

``allowed_clouds``
~~~~~~~~~~~~~~~~~~

Allow list for clouds to be used in ``sky check``.

This field is used to restrict the clouds that SkyPilot will check and use
when running ``sky check``. Any cloud already enabled but not specified here
will be disabled on the next ``sky check`` run.
If this field is not set, SkyPilot will check and use all supported clouds.

Default: ``null`` (use all supported clouds).

.. _config-yaml-docker:

``docker``
~~~~~~~~~~~~~~~~~~~~

Additional Docker run options (optional).

When ``image_id: docker:<docker_image>`` is used in a task YAML, additional
run options for starting the Docker container can be specified here.
These options will be passed directly as command line args to ``docker run``,
see: https://docs.docker.com/reference/cli/docker/container/run/

The following run options are applied by default and cannot be overridden:

- ``--net=host``
- ``--cap-add=SYS_ADMIN``
- ``--device=/dev/fuse``
- ``--security-opt=apparmor:unconfined``
- ``--runtime=nvidia # Applied if nvidia GPUs are detected on the host``

.. _config-yaml-docker-run-options:

``docker.run_options``
~~~~~~~~~~~~~~~~~~~~~~

This field can be useful for mounting volumes and other advanced Docker
configuration. You can specify a list of arguments or a string, where the
former will be combined into a single string with spaces. The following is
an example option for mounting the Docker socket and increasing the size of ``/dev/shm``:

Example:

.. code-block:: yaml

  docker:
    run_options:
      - -v /var/run/docker.sock:/var/run/docker.sock
      - --shm-size=2g

.. _config-yaml-nvidia-gpus:

``nvidia_gpus``
~~~~~~~~~~~~~~~~

.. _config-yaml-nvidia-gpus-disable-ecc:

``nvidia_gpus.disable_ecc``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Disable ECC for NVIDIA GPUs (optional).

Set to true to disable ECC for NVIDIA GPUs during provisioning. This is
useful to improve the GPU performance in some cases (up to 30%
improvement). This will only be applied if a cluster is requested with
NVIDIA GPUs. This is best-effort -- not guaranteed to work on all clouds
e.g., RunPod and Kubernetes does not allow rebooting the node, though
RunPod has ECC disabled by default.

Note: this setting will cause a reboot during the first provisioning of
the cluster, which may take a few minutes.

Reference: `portal.nutanix.com/page/documents/kbs/details?targetId=kA00e000000LKjOCAW <https://portal.nutanix.com/page/documents/kbs/details?targetId=kA00e000000LKjOCAW>`_

Default: ``false``.

.. _config-yaml-admin-policy:

``admin_policy``
~~~~~~~~~~~~~~~~

Admin policy to be applied to all tasks (optional).

The policy class to be applied to all tasks, which can be used to validate
and mutate user requests.

This is useful for enforcing certain policies on all tasks, such as:

- Adding custom labels.
- Enforcing resource limits.
- Restricting cloud providers.
- Requiring spot instances.
- Setting autostop timeouts.

See :ref:`advanced-policy-config` for details.

Example:

.. code-block:: yaml

  admin_policy: my_package.SkyPilotPolicyV1

.. _config-yaml-provision:

``provision``
~~~~~~~~~~~~~

.. _config-yaml-provision-ssh-timeout:

``provision.ssh_timeout``
~~~~~~~~~~~~~~~~~~~~~~~~~

Timeout in seconds for SSH connection probing during provisioning (optional).

Cluster SSH connection is probed during provisioning to check if a cluster is up. This timeout
determines how long to wait for the connection to be established.

Default: ``10``.

.. _config-yaml-provision-install-conda:

``provision.install_conda``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Whether to install conda on the remote cluster (optional).

Skypilot clusters come with conda preinstalled for convenience.
When set to ``false``, SkyPilot will not install conda on the cluster.

Default: ``true``.

Example:

.. code-block:: yaml

  provision:
    install_conda: false

.. note::

  Default SkyPilot images often come with conda preinstalled.
  To fully avoid installing conda, use a custom Docker image that does not have conda preinstalled
  along with ``install_conda: false``.

.. _config-yaml-aws:

``aws``
~~~~~~~

Advanced AWS configuration (optional).

Apply to all new instances but not existing ones.

.. _config-yaml-aws-labels:

``aws.labels``
~~~~~~~~~~~~~~~

Tags to assign to all instances and buckets created by SkyPilot (optional).

Example use case: cost tracking by user/team/project.

Users should guarantee that these key-values are valid AWS tags, otherwise
errors from the cloud provider will be surfaced.

Example:

.. code-block:: yaml

  aws:
    labels:
      # (Example) AWS Migration Acceleration Program (MAP). This tag enables the
      # program's discounts.
      # Ref: https://docs.aws.amazon.com/mgn/latest/ug/map-program-tagging.html
      map-migrated: my-value
      # (Example) Useful for keeping track of who launched what.  An IAM role
      # can be restricted to operate on instances owned by a certain name.
      # Ref: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_examples_ec2_tag-owner.html
      #
      # NOTE: SkyPilot by default assigns a "skypilot-user: <username>" tag to
      # all AWS/GCP/Azure instances launched by SkyPilot.
      Owner: user-unique-name
      # Other examples:
      my-tag: my-value



.. _config-yaml-aws-vpc-names:

``aws.vpc_names``
~~~~~~~~~~~~~~~~~

VPCs to use in each region (optional).

If this is set, SkyPilot will attempt each VPC for failover in regions
that contain the attempted VPCs (provisioner automatically looks for such
regions). Regions without any matching VPCs will not be used to launch nodes.

It is possible to set either a ``string`` (one VPC), or a ``list`` (multiple
target VPCs).

Default: ``null`` (use the default VPC in each region).

.. _config-yaml-aws-use-internal-ips:

``aws.use_internal_ips``
~~~~~~~~~~~~~~~~~~~~~~~~

Should instances be assigned private IPs only? (optional).

Set to true to use private IPs to communicate between the local client and
any SkyPilot nodes. This requires the networking stack be properly set up.

When set to ``true``, SkyPilot will only use private subnets to launch nodes.
Private subnets are defined as those satisfying both of these properties:

  1. Subnets whose route tables have no routes to an internet gateway (IGW);

  2. Subnets that are configured to not assign public IPs by default
     (the ``map_public_ip_on_launch`` attribute is ``false``).

This flag is typically set together with ``vpc_names`` above and
``ssh_proxy_command`` or ``use_ssm`` below.

Default: ``false``.

.. _config-yaml-aws-use-ssm:

``aws.use_ssm``
~~~~~~~~~~~~~~~~

Use SSM to communicate with SkyPilot nodes. This flag is typically set together with ``vpc_names`` and
``use_internal_ips`` above. This is useful for launching clusters in private VPCs without public IPs, refer to :ref:`aws-ssm` for more details.

Default: ``false``.

.. _config-yaml-aws-ssh-proxy-command:

``aws.ssh_proxy_command``
~~~~~~~~~~~~~~~~~~~~~~~~~

SSH proxy command (optional).

Useful for using a jump server to communicate with SkyPilot nodes hosted
in private VPC/subnets without public IPs. Typically set together with
``vpc_names`` and ``use_internal_ips`` above.

If set, this is passed as the ``-o ProxyCommand`` option for any SSH
connections (including rsync) used to communicate between the local client
and any SkyPilot nodes. (This option is not used between SkyPilot nodes,
since they are behind the proxy / may not have such a proxy set up.)

Default: ``null``.

Format 1:
  A string; the same proxy command is used for all regions.
Format 2:
  A dict mapping region names to region-specific proxy commands.
  NOTE: This restricts SkyPilot's search space for this cloud to only use
  the specified regions and not any other regions in this cloud.

Example:

.. code-block:: yaml

  aws:
    # Format 1
    ssh_proxy_command: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no ec2-user@<jump server public ip>

    # Format 2
    ssh_proxy_command:
      us-east-1: ssh -W %h:%p -p 1234 -o StrictHostKeyChecking=no myself@my.us-east-1.proxy
      us-east-2: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no ec2-user@<jump server public ip>

.. _config-yaml-aws-security-group-name:

``aws.security_group_name``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Security group (optional).

Security group name to use for AWS instances. If not specified,
SkyPilot will use the default name for the security group: ``sky-sg-<hash>``

Note: please ensure the security group name specified exists in the
regions the instances are going to be launched or the AWS account has the
permission to create a security group.

Some example use cases are shown below. All fields are optional.

- ``<string>``: Apply the service account with the specified name to all instances.

- ``<list of single-element dict>``: A list of single-element dictionaries mapping
  from the cluster name (pattern) to the security group name to use. The matching
  of the cluster name is done in the same order as the list.

  NOTE: If none of the wildcard expressions in the dictionary match the cluster
  name, SkyPilot will use the default security group name as mentioned above:
  ``sky-sg-<hash>``. To specify your default, use ``*`` as the wildcard expression.

Example:

.. code-block:: yaml

  aws:
    # Format 1
    security_group_name: my-security-group

    # Format 2
    security_group_name:
      - my-cluster-name: my-security-group-1
      - sky-serve-controller-*: my-security-group-2
      - "*": my-default-security-group

.. _config-yaml-aws-disk-encrypted:

``aws.disk_encrypted``
~~~~~~~~~~~~~~~~~~~~~~

Encrypted boot disk (optional).

Set to ``true`` to encrypt the boot disk of all AWS instances launched by
SkyPilot. This is useful for compliance with data protection regulations.

Default: ``false``.

.. _config-yaml-aws-ssh-user:

``aws.ssh_user``
~~~~~~~~~~~~~~~~

SSH user (optional) for the SkyPilot nodes.

Default: ``ubuntu``.

.. _config-yaml-aws-prioritize-reservations:

``aws.prioritize_reservations``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reserved capacity (optional).

Whether to prioritize capacity reservations (considered as 0 cost) in the
optimizer.

If you have capacity reservations in your AWS project:
Setting this to ``true`` guarantees the optimizer will pick any matching
reservation within all regions and AWS will auto consume your reservations
with instance match criteria to "open", and setting to ``false`` means
optimizer uses regular, non-zero pricing in optimization (if by chance any
matching reservation exists, AWS will still consume the reservation).

Note: this setting is default to ``false`` for performance reasons, as it can
take half a minute to retrieve the reservations from AWS when set to ``true``.

Default: ``false``.

.. _config-yaml-aws-specific-reservations:

``aws.specific_reservations``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The targeted capacity reservations (``CapacityReservationId``) to be
considered when provisioning clusters on AWS. SkyPilot will automatically
prioritize this reserved capacity (considered as zero cost) if the
requested resources matches the reservation.

Ref: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-launch.html

Example:

.. code-block:: yaml

  aws:
    specific_reservations:
      - cr-a1234567
      - cr-b2345678

.. _config-yaml-aws-remote-identity:

``aws.remote_identity``
~~~~~~~~~~~~~~~~~~~~~~~

Identity to use for AWS instances (optional).

Supported values:

1. **LOCAL_CREDENTIALS**:
   The user's local credential files will be uploaded to AWS instances created by SkyPilot.
   These credentials are used for:

   - Accessing cloud resources (e.g., private buckets).
   - Launching new instances (e.g., for jobs/serve controllers).

2. **SERVICE_ACCOUNT**:
   Local credential files are **not** uploaded to AWS instances. Instead:
   - SkyPilot will auto-create and reuse a service account (IAM role) for AWS instances.

3. **NO_UPLOAD**:
   No credentials will be uploaded to instances.
   This is useful to avoid overriding any existing credentials that may already be automounted on the cluster.

4. **Customized service account (IAM role)**:
   Specify this as either a ``<string>`` or a ``<list of single-element dict>``:

   - **<string>**: Apply the service account with the specified name to all instances.
   - **<list of single-element dict>**: A list of single-element dictionaries mapping cluster names (patterns) to service account names.

     * Matching of cluster names is done in the same order as the list.
     * If no wildcard expression matches the cluster name, ``LOCAL_CREDENTIALS`` will be used.
     * To specify a default, use ``*`` as the wildcard expression.

---

**Caveats for SERVICE_ACCOUNT with multicloud users**

1. This setting only affects AWS instances.
   Local AWS credentials will still be uploaded to **non-AWS instances** (since those may need access to AWS resources).
   To fully disable credential uploads, set ``remote_identity: NO_UPLOAD``.

2. If the SkyPilot jobs/serve controller is on AWS:
   - Non-AWS managed jobs or non-AWS service replicas will fail to access AWS resources.
   - This occurs because the controllers won't have AWS credential files to assign to these non-AWS instances.

---

**Example configuration**

.. code-block:: yaml

  aws:
    # Format 1
    remote_identity: my-service-account-name

    # Format 2
    remote_identity:
      - my-cluster-name: my-service-account-1
      - sky-serve-controller-*: my-service-account-2
      - "*": my-default-service-account

.. _config-yaml-aws-post-provision-runcmd:

``aws.post_provision_runcmd``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run commands during the instance initialization phase (optional).

This is executed through cloud-init's ``runcmd``, which is useful for doing any setup that must happen right after the instance starts, such as:

- Configuring system settings
- Installing certificates
- Installing packages

Each item can be either a string or a list.

Example:

.. code-block:: yaml

  aws:
    post_provision_runcmd:
      - echo "hello world!"
      - [ls, -l, /]

.. _config-yaml-aws-capabilities:

``aws.capabilities``
~~~~~~~~~~~~~~~~~~~~

Capabilities to enable for AWS. Used to enable specific features of AWS
(e.g. only use AWS for S3 but not for launching VMs)

Default: ``['compute', 'storage']``.

Example:

.. code-block:: yaml

  aws:
    capabilities:
      - storage

.. _config-yaml-gcp:

``gcp``
~~~~~~~

Advanced GCP configuration (optional).

Apply to all new instances but not existing ones.

.. _config-yaml-gcp-labels:

``gcp.labels``
~~~~~~~~~~~~~~~~

Labels to assign to all instances launched by SkyPilot (optional).

Example use case: cost tracking by user/team/project.

Users should guarantee that these key-values are valid GCP labels, otherwise
errors from the cloud provider will be surfaced.

Example:

.. code-block:: yaml

  gcp:
    labels:
      Owner: user-unique-name
      my-label: my-value

.. _config-yaml-gcp-vpc-name:

``gcp.vpc_name``
~~~~~~~~~~~~~~~~

VPC to use (optional).

Default: ``null``, which implies the following behavior: All existing
VPCs in the project are checked against the minimal recommended firewall
rules for SkyPilot to function. If any VPC satisfies these rules, it is
used. Otherwise, a new VPC named ``skypilot-vpc`` is automatically created
with the minimal recommended firewall rules and will be used.

If this field is set, SkyPilot will use the VPC with this name. The VPC must
have the :ref:`necessary firewall rules <gcp-minimum-firewall-rules>`. Useful
for when users want to manually set up a VPC and precisely control its
firewall rules. If no region restrictions are given, SkyPilot only
provisions in regions for which a subnet of this VPC exists. Errors are
thrown if VPC with this name is not found. The VPC does not get modified
in any way, except when opening ports (e.g., via ``resources.ports``) in
which case new firewall rules permitting public traffic to those ports
will be added.

By default, only VPCs from the current project are used.

.. code-block:: yaml

  gcp:
    vpc-name: my-vpc

To use a shared VPC from another GCP project, specify the name as ``<project ID>/<vpc name>``. For example:

.. code-block:: yaml

  gcp:
    vpc-name: my-project-123456/default

.. _config-yaml-gcp-use-internal-ips:

``gcp.use_internal_ips``
~~~~~~~~~~~~~~~~~~~~~~~~

Should instances be assigned private IPs only? (optional).

Set to ``true`` to use private IPs to communicate between the local client and
any SkyPilot nodes. This requires the networking stack be properly set up.

This flag is typically set together with ``vpc_name`` above and
``ssh_proxy_command`` below.

Default: ``false``.

.. _config-yaml-gcp-force-enable-external-ips:

``gcp.force_enable_external_ips``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Should instances in a vpc where communicated with via internal IPs still
have an external IP? (optional).

Set to ``true`` to force VMs to be assigned an external IP even when
``vpc_name`` and ``use_internal_ips`` are set.

Default: ``false``.

.. _config-yaml-gcp-ssh-proxy-command:

``gcp.ssh_proxy_command``
~~~~~~~~~~~~~~~~~~~~~~~~~

SSH proxy command (optional).

Please refer to the :ref:`aws.ssh_proxy_command <config-yaml-aws-ssh-proxy-command>` section above for more details.

Format 1:
  A string; the same proxy command is used for all regions.
Format 2:
  A dict mapping region names to region-specific proxy commands.
  NOTE: This restricts SkyPilot's search space for this cloud to only use
  the specified regions and not any other regions in this cloud.

Example:

.. code-block:: yaml

  gcp:
    # Format 1
    ssh_proxy_command: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no gcpuser@<jump server public ip>

    # Format 2
    ssh_proxy_command:
      us-central1: ssh -W %h:%p -p 1234 -o StrictHostKeyChecking=no myself@my.us-central1.proxy
      us-west1: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no gcpuser@<jump server public ip>

.. _config-yaml-gcp-prioritize-reservations:

``gcp.prioritize_reservations``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reserved capacity (optional).

Whether to prioritize reserved instance types/locations (considered as 0
cost) in the optimizer.

If you have "automatically consumed" reservations in your GCP project:
  - Setting this to ``true`` guarantees the optimizer will pick any matching
    reservation and GCP will auto consume your reservation, and setting to
    ``false`` means optimizer uses regular, non-zero pricing in optimization (if
    by chance any matching reservation exists, GCP still auto consumes the
    reservation).

If you have "specifically targeted" reservations (set by the ``specific_reservations`` field below):
  - This field will automatically be set to ``true``.

Note: this setting is default to ``false`` for performance reasons, as it can
take half a minute to retrieve the reservations from GCP when set to ``true``.

Default: ``false``.

.. _config-yaml-gcp-specific-reservations:

``gcp.specific_reservations``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The "specifically targeted" reservations to be considered when provisioning
clusters on GCP. SkyPilot will automatically prioritize this reserved
capacity (considered as zero cost) if the requested resources matches the
reservation.

Ref: https://cloud.google.com/compute/docs/instances/reservations-overview#consumption-type

Example:

.. code-block:: yaml

  gcp:
    specific_reservations:
      - projects/my-project/reservations/my-reservation1
      - projects/my-project/reservations/my-reservation2

.. _config-yaml-gcp-managed-instance-group:

``gcp.managed_instance_group``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Managed instance group / DWS (optional).

SkyPilot supports launching instances in a managed instance group (MIG)
which schedules the GPU instance creation through DWS, offering a better
availability. This feature is only applied when a resource request
contains GPU instances.

``run_duration``: Duration for a created instance to be kept alive (in seconds, required).
This is required for the DWS to work properly. After the specified duration,
the instance will be terminated.

``provision_timeout``: Timeout for provisioning an instance by DWS (in seconds, optional).
This timeout determines how long SkyPilot will wait for a managed instance
group to create the requested resources before giving up, deleting the MIG
and failing over to other locations. Larger timeouts may increase the chance
for getting a resource, but will block failover to go to other zones/regions/clouds.

Default: ``900``.

Example:

.. code-block:: yaml

  gcp:
    managed_instance_group:
      run_duration: 3600
      provision_timeout: 900

.. _config-yaml-gcp-remote-identity:

``gcp.remote_identity``
~~~~~~~~~~~~~~~~~~~~~~~

Identity to use for GCP instances (optional).

Please refer to the aws.remote_identity section above for more details.

Default: ``LOCAL_CREDENTIALS``.

.. _config-yaml-gcp-enable-gvnic:

``gcp.enable_gvnic``
~~~~~~~~~~~~~~~~~~~~

Enable gVNIC network interface (optional).

Set to true to enable gVNIC network interface for all GCP instances
launched by SkyPilot. This is useful for improving network performance.

Default: ``false``.

.. _config-yaml-gcp-enable-gpu-direct:

``gcp.enable_gpu_direct``
~~~~~~~~~~~~~~~~~~~~~~~~~

Enable GPUDirect-TCPX, a high-performance networking technology that establishes direct communication between GPUs and network interfaces for `a3-highgpu-8g` or `a3-edgegpu-8g` instances launched by SkyPilot. When enabled, this configuration automatically activates the gVNIC network interface for optimal performance.

Default: ``false``.

.. _config-yaml-gcp-placement-policy:

``gcp.placement_policy``
~~~~~~~~~~~~~~~~~~~~~~~~

Placement policy for GCP instances. This setting controls how instances are physically placed within a data center to optimize performance and resource utilization.

When `gcp.enable_gpu_direct` is enabled, the placement policy is automatically set to `compact` to ensure optimal communication performance. If `gcp.enable_gpu_direct` is disabled, no default placement policy is applied.

Refer to the `GCP documentation <https://cloud.google.com/compute/docs/instances/placement-policies-overview>`_ for more information on placement policies.

.. _config-yaml-gcp-capabilities:

``gcp.capabilities``
~~~~~~~~~~~~~~~~~~~~

Capabilities to enable for the GCP.

Used to enable specific features of GCP
(e.g. only use GCP for GCS but not for launching VMs)

Default: ``['compute', 'storage']``.

Example:

.. code-block:: yaml

  gcp:
    capabilities:
      - storage

.. _config-yaml-azure:

``azure``
~~~~~~~~~~~

Advanced Azure configuration (optional).

.. _config-yaml-azure-resource-group-vm:

``azure.resource_group_vm``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Resource group for VM resources (optional).

Name of the resource group to use for VM resources. If not specified,
SkyPilot will create a new resource group with a default name.

.. _config-yaml-azure-storage-account:

``azure.storage_account``
~~~~~~~~~~~~~~~~~~~~~~~~~

Storage account name (optional).

Name of the storage account to use. If not specified, SkyPilot will
create a new storage account with a default name.

Example:

.. code-block:: yaml

  azure:
    resource_group_vm: user-resource-group-name
    storage_account: user-storage-account-name

.. _config-yaml-kubernetes:

``kubernetes``
~~~~~~~~~~~~~~~

Advanced Kubernetes configuration (optional).

.. _config-yaml-kubernetes-ports:

``kubernetes.ports``
~~~~~~~~~~~~~~~~~~~~

Port configuration mode (optional).

Can be one of:

- ``loadbalancer``: Use LoadBalancer service to expose ports.
- ``nodeport``: Use NodePort service to expose ports.
- ``podip``: Use Pod IPs to expose ports. Cannot be accessed from outside the cluster.

Default: ``loadbalancer``.

.. _config-yaml-kubernetes-remote-identity:

``kubernetes.remote_identity``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Service account for remote authentication (optional).

Name of the service account to use for remote authentication.

.. _config-yaml-kubernetes-allowed-contexts:

``kubernetes.allowed_contexts``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

List of allowed Kubernetes contexts (optional).

List of context names that SkyPilot is allowed to use.

If you want all available contexts to be allowed, set it to 'all' like this:

.. code-block:: yaml

  kubernetes:
    allowed_contexts: all


You can also set ``SKYPILOT_ALLOW_ALL_KUBERNETES_CONTEXTS`` environment variable to ``"true"``
for the same effect. Configuration option overrides the environment variable if set.

.. _config-yaml-kubernetes-custom-metadata:

``kubernetes.custom_metadata``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Custom metadata for Kubernetes resources (optional).

Custom labels and annotations to apply to all Kubernetes resources.

.. _config-yaml-kubernetes-provision-timeout:

``kubernetes.provision_timeout``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Timeout for resource provisioning (optional).

Timeout in seconds for resource provisioning.

Default: ``10``.

.. _config-yaml-kubernetes-autoscaler:

``kubernetes.autoscaler``
~~~~~~~~~~~~~~~~~~~~~~~~~

Autoscaler type (optional).

Type of autoscaler used by the underlying Kubernetes cluster. Used to configure the GPU labels used by the pods submitted by SkyPilot.

Can be one of:

- ``gke``: Google Kubernetes Engine
- ``karpenter``: Karpenter
- ``coreweave``: `CoreWeave autoscaler <https://docs.coreweave.com/docs/products/cks/nodes/autoscaling>`_
- ``nebius``: `Nebius autoscaler <https://docs.nebius.com/kubernetes/node-groups/autoscaling>`_
- ``generic``: Generic autoscaler, assumes nodes are labelled with ``skypilot.co/accelerator``.

If you want to use the autoscaler, set :ref:`provision_timeout <config-yaml-kubernetes-provision-timeout>` to at least 600.

.. _config-yaml-kubernetes-pod-config:

``kubernetes.pod_config``
~~~~~~~~~~~~~~~~~~~~~~~~~

Pod configuration settings (optional).

Additional pod configuration settings to apply to all pods.

Example:

.. code-block:: yaml

  kubernetes:
    networking: portforward
    ports: loadbalancer
    remote_identity: my-k8s-service-account
    allowed_contexts:
      - context1
      - context2
    custom_metadata:
      labels:
        mylabel: myvalue
      annotations:
        myannotation: myvalue
    provision_timeout: 10
    autoscaler: gke
    set_pod_resource_limits: true  # or a multiplier like 1.5
    pod_config:
      metadata:
        labels:
          my-label: my-value
      spec:
        runtimeClassName: nvidia
        imagePullSecrets:
          - name: my-secret
        containers:
          - env:
              - name: HTTP_PROXY
                value: http://proxy-host:3128
            volumeMounts:
              - mountPath: /foo
                name: example-volume
                readOnly: true
        volumes:
          - name: example-volume
            hostPath:
                path: /tmp
                type: Directory
          - name: dshm
            emptyDir:
                medium: Memory
                sizeLimit: 3Gi

By default, SkyPilot automatically creates a single container named ``ray-node`` in the Pod. While you typically don't need to explicitly set the container name, if you do specify ``pod_config.spec.containers[0].name``, it must be set to ``ray-node``:

.. code-block:: yaml

  kubernetes:
    pod_config:
      spec:
        containers:
          - name: ray-node
            ...

.. _config-yaml-kubernetes-kueue:

``kubernetes.kueue``
~~~~~~~~~~~~~~~~~~~~~

Kueue configuration (optional).

.. _config-yaml-kubernetes-kueue-local-queue-name:

``kubernetes.kueue.local_queue_name``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Name of the `local queue <https://kueue.sigs.k8s.io/docs/concepts/local_queue/>`_ to use for SkyPilot jobs.

.. _config-yaml-kubernetes-dws:

``kubernetes.dws``
~~~~~~~~~~~~~~~~~~

GKE DWS configuration (optional).

Refer to :ref:`Using DWS on GKE <dws-on-gke>` for more details.

``kubernetes.dws.enabled``
~~~~~~~~~~~~~~~~~~~~~~~~~~

Whether to enable DWS (optional).

When ``enabled: true``, SkyPilot will automatically use DWS with flex-start mode. If ``kubernetes.kueue.local_queue_name`` is set, it will use flex-start with queued provisioning mode.

Default: ``false``.

``kubernetes.dws.max_run_duration``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Maximum runtime of a node (optional), only used in ``flex-start-queued-provisioning`` mode.

Default: ``null``.

Example:

.. code-block:: yaml

  kubernetes:
    dws:
      enabled: true
      max_run_duration: 10m

.. _config-yaml-kubernetes-post-provision-runcmd:

``kubernetes.post_provision_runcmd``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run commands during the instance initialization phase (optional).

This is executed before any of the SkyPilot runtime setup commands, which is useful for doing any setup that must happen right after the instance starts, such as:

- Configuring system settings
- Installing certificates

Each item is a string.

Example:

.. code-block:: yaml

  kubernetes:
    post_provision_runcmd:
      - echo "hello world!"

.. _config-yaml-kubernetes-set-pod-resource-limits:

``kubernetes.set_pod_resource_limits``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Set pod CPU/memory limits relative to requests (optional).

This is useful for Kubernetes clusters that require resource limits to be set
(e.g., for LimitRange enforcement, resource quotas, or cluster policies).

Can be one of:

- ``false`` (default): Do not set CPU/memory limits (only requests are set).
- ``true``: Set limits equal to requests (multiplier of 1).
- A positive number: Set limits to requests multiplied by this value (e.g., ``1.5`` for 50% headroom).

Default: ``false``.

Example:

.. code-block:: yaml

  kubernetes:
    # Set limits equal to requests
    set_pod_resource_limits: true

.. code-block:: yaml

  kubernetes:
    # Set limits to 1.5x requests (50% headroom)
    set_pod_resource_limits: 1.5

This can also be configured per-context using ``context_configs``:

.. code-block:: yaml

  kubernetes:
    context_configs:
      prod-cluster:
        set_pod_resource_limits: 2.0

.. _config-yaml-kubernetes-context-configs:

``kubernetes.context_configs``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Context-specific configuration for Kubernetes resources (optional).

When using multiple Kubernetes contexts, you can specify context-specific configuration for Kubernetes resources.

Example:

.. code-block:: yaml

  kubernetes:
    context_configs:
      context1:
        pod_config:
          metadata:
            labels:
              my-label: my-value
      context2:
        remote_identity: my-k8s-service-account

When a config field is specified for both the ``kubernetes`` and specific context ``kubernetes.context_configs.context-name``,
the context-specific config overrides the general config according to the :ref:`config-overrides` rules.

.. _config-yaml-ssh:

``ssh``
~~~~~~~

Advanced SSH node pool configuration (optional).

.. _config-yaml-ssh-allowed-node-pools:

``ssh.allowed_node_pools``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

List of allowed SSH node pools (optional).

List of names that SkyPilot is allowed to use.

.. _config-yaml-slurm:

``slurm``
~~~~~~~~~

Advanced Slurm configuration (optional).

.. _config-yaml-slurm-allowed-clusters:

``slurm.allowed_clusters``
~~~~~~~~~~~~~~~~~~~~~~~~~~

List of allowed Slurm clusters (optional).

List of cluster names that SkyPilot is allowed to use.

If you want all available clusters to be allowed, set it to ``all`` like this:

.. code-block:: yaml

  slurm:
    allowed_clusters: all

.. _config-yaml-slurm-provision-timeout:

``slurm.provision_timeout``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Timeout for Slurm job allocation (optional).

Timeout in seconds for waiting on Slurm to allocate nodes for a launched job.
If the timeout is reached, SkyPilot will fail over to other Slurm
partitions or clusters.

.. note::

  If your Slurm cluster has long queueing delays, consider increasing
  ``slurm.provision_timeout``. This helps avoid premature failover while Slurm
  is still working to allocate resources.

Default:

- ``120`` seconds (2 minutes) when a partition is not specified
- ``86400`` seconds (24 hours) when a specific partition is specified

Set to a negative value (e.g., ``-1``) to wait indefinitely.

Example:

.. code-block:: yaml

  slurm:
    provision_timeout: 1200

.. _config-yaml-oci:

``oci``
~~~~~~~

Advanced OCI configuration (optional).

``oci_config_profile``
    The profile name in ``~/.oci/config`` to use for launching instances.
    Default: ``DEFAULT``

``compartment_ocid``
    The OCID of the compartment to use for launching instances. If not set, the root compartment will be used (optional).

``image_tag_general``
    The default image tag to use for launching general instances (CPU) if the ``image_id`` parameter is not specified.
    Default: ``skypilot:cpu-ubuntu-2204``

``image_tag_gpu``
    The default image tag to use for launching GPU instances if the ``image_id`` parameter is not specified.
    Default: ``skypilot:gpu-ubuntu-2204``

The configuration can be specified either in the ``default`` section (applying to all regions unless overridden) or in region-specific sections.

Example:

.. code-block:: yaml

    oci:
        # Region-specific configuration
        region_configs:
          ap-seoul-1:
            # The OCID of the VCN to use for instances (optional).
            vcn_ocid: ocid1.vcn.oc1.ap-seoul-1.amaaaaaaak7gbriarkfs2ssus5mh347ktmi3xa72tadajep6asio3ubqgarq
            # The OCID of the subnet to use for instances (optional).
            vcn_subnet: ocid1.subnet.oc1.ap-seoul-1.aaaaaaaa5c6wndifsij6yfyfehmi3tazn6mvhhiewqmajzcrlryurnl7nuja

          us-ashburn-1:
            vcn_ocid: ocid1.vcn.oc1.ap-seoul-1.amaaaaaaak7gbriarkfs2ssus5mh347ktmi3xa72tadajep6asio3ubqgarq
            vcn_subnet: ocid1.subnet.oc1.iad.aaaaaaaafbj7i3aqc4ofjaapa5edakde6g4ea2yaslcsay32cthp7qo55pxa

.. _config-yaml-nebius:

``nebius``
~~~~~~~~~~

Advanced Nebius configuration (optional).

``project_id``
    Identifier for the Nebius project (optional)
    Default: Uses first available project if not specified

``fabric``
    GPU cluster configuration identifier (optional)
    Optional: GPU cluster disabled if not specified

``filesystems``
    List of filesystems to mount on the nodes (optional).
    Each filesystem is a dict with the following keys:

    - ``filesystem_id``: ID of the filesystem to mount. Required for each filesystem.
    - ``filesystem_attach_mode``: Attach mode for the filesystem.

      Allowed values: ``READ_WRITE``, ``READ_ONLY``. Defaults to ``READ_WRITE``.
    - ``filesystem_mount_path``: Path to mount the filesystem on the nodes.

      Defaults to ``/mnt/filesystem-skypilot-<index>``.

The configuration must be specified in the per-region config section: ``region_configs``.

Example:

.. code-block:: yaml

    nebius:
        use_internal_ips: true
        use_static_ip_address: true
        ssh_proxy_command:
          eu-north1: ssh -W %h:%p -p 1234 -o StrictHostKeyChecking=no myself@my.us-central1.proxy
          eu-west1: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no nebiususer@<jump server public ip>
        tenant_id: tenant-1234567890
        # Region-specific configuration
        region_configs:
            eu-north1:
                # Project identifier for this region
                # Optional: Uses first available project if not specified
                project_id: project-e00......
                # GPU cluster fabric identifier
                # Optional: GPU cluster disabled if not specified
                fabric: fabric-3
            eu-west1:
                project_id: project-e01...
                fabric: fabric-5
                filesystems:
                  - filesystem_id: computefilesystem-e00aaaaa01bbbbbbbb
                    mount_path: /mnt/fsnew
                    attach_mode: READ_WRITE
                  - filesystem_id: computefilesystem-e00ccccc02dddddddd
                    mount_path: /mnt/fsnew2
                    attach_mode: READ_ONLY


.. _config-yaml-nebius-use-internal-ips:

``nebius.use_internal_ips``
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Should instances be assigned private IPs only? (optional).

Set to ``true`` to use private IPs to communicate between the local client and
any SkyPilot nodes. This requires the networking stack be properly set up.

This flag is typically set together with ``ssh_proxy_command`` below.

Default: ``false``.

.. _config-yaml-nebius-use-static-ip-address:

``nebius.use_static_ip_address``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Should instances be assigned static IPs? (optional).

Set to ``true`` to use static IPs.

Default: ``false``.

.. _config-yaml-nebius-ssh-proxy-command:

``nebius.ssh_proxy_command``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SSH proxy command (optional).

Please refer to the :ref:`aws.ssh_proxy_command <config-yaml-aws-ssh-proxy-command>` section above for more details.

Format 1:
  A string; the same proxy command is used for all regions.
Format 2:
  A dict mapping region names to region-specific proxy commands.
  NOTE: This restricts SkyPilot's search space for this cloud to only use
  the specified regions and not any other regions in this cloud.

Example:

.. code-block:: yaml

  nebius:
    # Format 1
    ssh_proxy_command: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no nebiususer@<jump server public ip>

    # Format 2
    ssh_proxy_command:
      eu-north1: ssh -W %h:%p -p 1234 -o StrictHostKeyChecking=no myself@my.us-central1.proxy
      eu-west1: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no nebiususer@<jump server public ip>

.. _config-yaml-nebius-tenant-id:

``nebius.tenant_id``
~~~~~~~~~~~~~~~~~~~~

Nebius tenant ID (optional).

Example:

.. code-block:: yaml

  nebius:
    tenant_id: tenant-1234567890

.. _config-yaml-nebius-domain:

``nebius.domain``
~~~~~~~~~~~~~~~~~~~~

Nebius API domain (optional).

Example:

.. code-block:: yaml

  nebius:
    domain: api.nebius.cloud:443

.. _config-yaml-vast:

``vast``
~~~~~~~~

Advanced Vast configuration (optional).

.. _config-yaml-vast-datacenter-only:

``vast.datacenter_only``
~~~~~~~~~~~~~~~~~~~~~~~~

Configure SkyPilot to only consider offers on Vast verified datacenters (optional).
Internally, this will query Vast with the ``datacenter=true`` and ``hosting_type>=1``
parameters to filter for professional datacenter-hosted machines. Note some GPUs
may only be available on non-datacenter offers. This config filters both the catalog
(during resource planning) and the launch query (during provisioning). This config
can be overridden per task via :ref:`config flag <config-client-cli-flag>`.

Default: ``false``

.. _config-yaml-vast-create-instance-kwargs:

``vast.create_instance_kwargs``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Additional parameters to pass to the Vast API when creating instances (optional).

This allows full access to Vast's instance creation options. User-provided
parameters are passed through to the Vast API.

.. dropdown:: Supported parameters

    ``image``
        Docker image to use for the instance. If not specified, SkyPilot uses the image from the task
        configuration. When using a template, this is not required. (e.g vastai/base-image:@vastai-automatic-tag)

    ``env``
        Environment variables and port mappings (e.g., ``"-e KEY=value -p 8080:8080"``).

    ``price`` / ``bid_price``
        Bid price for the instance. For preemptible instances, if not specified,
        SkyPilot uses the minimum bid price from the offer.

    ``disk``
        Disk size in GB. If not specified, SkyPilot uses the disk size from the
        task configuration.

    ``label``
        Instance label. If not specified, SkyPilot uses the cluster name
        (e.g., ``cluster-name-head`` or ``cluster-name-worker``).

    ``extra``
        Extra docker run arguments to pass to the container.

    ``onstart_cmd``
        Command to run on instance start. SkyPilot prepends its own initialization
        commands to this.

    ``onstart``
        Path to a local script file to run on instance start. The file contents
        are read and appended to ``onstart_cmd``.

    ``login``
        Docker registry login credentials (e.g., ``"-u user -p pass registry"``).
        Required when using private Docker registries.

    ``image_login``
        Docker registry credentials if needed.
        Required when using private Docker registries.

    ``python_utf8``
        Enable Python UTF-8 mode (boolean true | false).

    ``lang_utf8``
        Enable system UTF-8 locale (boolean true | false).

    ``jupyter_lab``
        Use JupyterLab instead of Jupyter Notebook (boolean true | false).

    ``jupyter_dir``
        Jupyter notebook directory path.

    ``force``
        Force instance creation even if warnings are present (boolean true | false).

    ``cancel_unavail``
        Cancel the request if the instance becomes unavailable (boolean true | false).

    ``template_hash`` / ``template_hash_id``
        Use a Vast template by its hash ID. When specified, ``image`` and ``disk``
        are not required as they come from the template.

    ``args``
        Custom docker command arguments as a list of strings.

    ``user``
        Run the container as a specific user.

    ``vm``
        Whether this is a VM instance. (boolean true | false)

Example:

.. code-block:: yaml

  vast:
    datacenter_only: true
    create_instance_kwargs:
      python_utf8: true
      lang_utf8: true
      extra: "--shm-size=16g"
      onstart_cmd: "echo 'Instance started'"

Example using a Vast template:

.. code-block:: yaml

  vast:
    create_instance_kwargs:
      template_hash_id: "abc123def456"
      price: 0.50

.. _config-yaml-rbac:

``rbac``
~~~~~~~~

RBAC configuration (optional).

.. _config-yaml-rbac-default-role:

``rbac.default_role``
~~~~~~~~~~~~~~~~~~~~~

Default role for users (optional).  Either ``admin`` or ``user``.

If not specified, the default role is ``admin``.

.. TODO(aylei): Refine this after unified authentication.

Note: RBAC is only functional when :ref:`OAuth <api-server-oauth>` is configured.

.. _config-yaml-db:

``db``
~~~~~~

API Server database configuration (optional).

Specify the database connection string to use for SkyPilot. If not specified,
SkyPilot will use a SQLite database initialized in the ``~/.sky`` directory.

If a PostgreSQL database URL is specified, SkyPilot will use the database to
persist API server state.

Currently, managed job controller state is not persisted in remote databases
even if ``db`` is specified.

.. note::

  (available on nightly version 20250626 and later)

  ``db`` configuration can also be set using the ``SKYPILOT_DB_CONNECTION_URI`` environment variable.

  This is optional. For larger deployments (for example, many nodes/clusters
  and many pending jobs), consider configuring a PostgreSQL backend via
  ``db`` or ``SKYPILOT_DB_CONNECTION_URI``.

.. note::

  If ``db`` is specified in the config, no other configuration parameter can be specified in the SkyPilot config file.

  Other configuration parameters can be set in the "Workspaces" tab of the web dashboard.

Example:

.. code-block:: yaml

  db: postgresql://postgres@localhost/skypilot

.. toctree::
   :hidden:

   Configuration Sources <config-sources>

.. _config-yaml-logs:

``logs``
~~~~~~~~

External logging storage configuration (optional).

.. code-block:: yaml

  logs:
    store: gcp
    gcp:
      project_id: my-project-id

.. _config-yaml-logs-store:

``logs.store``
~~~~~~~~~~~~~~

The type of external logging storage to use. Each logging storage might have its own configuration options under ``logs.<store>`` structure. Refer to the :ref:`External Logging Storage <external-logging-storage>` for more details.

.. code-block:: yaml

  logs:
    store: gcp

.. _config-yaml-daemons:

``daemons``
~~~~~~~~~~~

Daemon configuration (optional).

Configuration for API server daemons. Not applicable to client side config.

Valid daemon names are:

- ``skypilot-status-refresh-daemon``
- ``skypilot-volume-status-refresh-daemon``
- ``managed-job-status-refresh-daemon``

``log_level``
    Log level to set for the daemon. Valid values are ``DEBUG``, ``INFO`` and ``WARNING``.

.. code-block:: yaml

  daemons:
    skypilot-status-refresh-daemon:
      log_level: DEBUG
    skypilot-volume-status-refresh-daemon:
      log_level: INFO
    managed-job-status-refresh-daemon:
      log_level: WARNING

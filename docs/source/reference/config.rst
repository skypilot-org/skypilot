.. _config-yaml:

Advanced Configurations
===========================

You can pass **optional configurations** to SkyPilot in the ``~/.sky/config.yaml`` file.

Such configurations apply to all new clusters and do not affect existing clusters.

Spec: ``~/.sky/config.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Available fields and semantics:

.. code-block:: yaml

  # Custom managed jobs controller resources (optional).
  #
  # These take effects only when a managed jobs controller does not already exist.
  #
  # Ref: https://skypilot.readthedocs.io/en/latest/examples/managed-jobs.html#customizing-job-controller-resources
  jobs:
    controller:
      resources:  # same spec as 'resources' in a task YAML
        cloud: gcp
        region: us-central1
        cpus: 4+  # number of vCPUs, max concurrent spot jobs = 2 * cpus
        disk_size: 100

  # Advanced AWS configurations (optional).
  # Apply to all new instances but not existing ones.
  aws:
    # Tags to assign to all instances launched by SkyPilot (optional).
    #
    # Example use case: cost tracking by user/team/project.
    #
    # Users should guarantee that these key-values are valid AWS tags, otherwise
    # errors from the cloud provider will be surfaced.
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

    # VPC to use in each region (optional).
    #
    # If this is set, SkyPilot will only provision in regions that contain a VPC
    # with this name (provisioner automatically looks for such regions).
    # Regions without a VPC with this name will not be used to launch nodes.
    #
    # Default: null (use the default VPC in each region).
    vpc_name: skypilot-vpc

    # Should instances be assigned private IPs only? (optional)
    #
    # Set to true to use private IPs to communicate between the local client and
    # any SkyPilot nodes. This requires the networking stack be properly set up.
    #
    # When set to true, SkyPilot will only use private subnets to launch nodes.
    # Private subnets are defined as those satisfying both of these properties:
    #   1. Subnets whose route tables have no routes to an internet gateway (IGW);
    #   2. Subnets that are configured to not assign public IPs by default
    #       (the `map_public_ip_on_launch` attribute is False).
    #
    # This flag is typically set together with 'vpc_name' above and
    # 'ssh_proxy_command' below.
    #
    # Default: false.
    use_internal_ips: true

    # SSH proxy command (optional).
    #
    # Useful for using a jump server to communicate with SkyPilot nodes hosted
    # in private VPC/subnets without public IPs. Typically set together with
    # 'vpc_name' and 'use_internal_ips' above.
    #
    # If set, this is passed as the '-o ProxyCommand' option for any SSH
    # connections (including rsync) used to communicate between the local client
    # and any SkyPilot nodes. (This option is not used between SkyPilot nodes,
    # since they are behind the proxy / may not have such a proxy set up.)
    #
    # Optional; default: null.
    ### Format 1 ###
    # A string; the same proxy command is used for all regions.
    ssh_proxy_command: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no ec2-user@<jump server public ip>
    ### Format 2 ###
    # A dict mapping region names to region-specific proxy commands.
    # NOTE: This restricts SkyPilot's search space for this cloud to only use
    # the specified regions and not any other regions in this cloud.
    ssh_proxy_command:
      us-east-1: ssh -W %h:%p -p 1234 -o StrictHostKeyChecking=no myself@my.us-east-1.proxy
      us-east-2: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no ec2-user@<jump server public ip>

    # Security group (optional).
    #
    # The name of the security group to use for all instances. If not specified,
    # SkyPilot will use the default name for the security group: sky-sg-<hash>
    # Note: please ensure the security group name specified exists in the
    # regions the instances are going to be launched or the AWS account has the
    # permission to create a security group.
    security_group_name: my-security-group

    # Identity to use for AWS instances (optional).
    #
    # LOCAL_CREDENTIALS: The user's local credential files will be uploaded to
    # AWS instances created by SkyPilot. They are used for accessing cloud
    # resources (e.g., private buckets) or launching new instances (e.g., for
    # jobs/serve controllers).
    #
    # SERVICE_ACCOUNT: Local credential files are not uploaded to AWS
    # instances. SkyPilot will auto-create and reuse a service account (IAM
    # role) for AWS instances.
    #
    # Customized service account (IAM role): <string> or <list of single-element dict>
    # - <string>: apply the service account with the specified name to all instances.
    #    Example:
    #       remote_identity: my-service-account-name
    # - <list of single-element dict>: A list of single-element dict mapping from the cluster name (pattern)
    #   to the service account name to use. The matching of the cluster name is done in the same order
    #   as the list.
    #   NOTE: If none of the wildcard expressions in the dict match the cluster name, LOCAL_CREDENTIALS will be used.
    #   To specify your default, use "*" as the wildcard expression.
    #   Example:
    #       remote_identity:
    #         - my-cluster-name: my-service-account-1
    #         - sky-serve-controller-*: my-service-account-2
    #         - "*": my-default-service-account
    #
    # Two caveats of SERVICE_ACCOUNT for multicloud users:
    #
    # - This only affects AWS instances. Local AWS credentials will still be
    #   uploaded to non-AWS instances (since those instances may need to access
    #   AWS resources).
    # - If the SkyPilot jobs/serve controller is on AWS, this setting will make
    #   non-AWS managed jobs / non-AWS service replicas fail to access any
    #   resources on AWS (since the controllers don't have AWS credential
    #   files to assign to these non-AWS instances).
    #
    # Default: 'LOCAL_CREDENTIALS'.
    remote_identity: LOCAL_CREDENTIALS

  # Advanced GCP configurations (optional).
  # Apply to all new instances but not existing ones.
  gcp:
    # Labels to assign to all instances launched by SkyPilot (optional).
    #
    # Example use case: cost tracking by user/team/project.
    #
    # Users should guarantee that these key-values are valid GCP labels, otherwise
    # errors from the cloud provider will be surfaced.
    labels:
      Owner: user-unique-name
      my-label: my-value

    # VPC to use (optional).
    #
    # Default: null, which implies the following behavior. First, all existing
    # VPCs in the project are checked against the minimal recommended firewall
    # rules for SkyPilot to function. If any VPC satisfies these rules, it is
    # used. Otherwise, a new VPC named 'skypilot-vpc' is automatically created
    # with the minimal recommended firewall rules and will be used.
    #
    # If this field is set, SkyPilot will use the VPC with this name. Useful for
    # when users want to manually set up a VPC and precisely control its
    # firewall rules. If no region restrictions are given, SkyPilot only
    # provisions in regions for which a subnet of this VPC exists. Errors are
    # thrown if VPC with this name is not found. The VPC does not get modified
    # in any way, except when opening ports (e.g., via `resources.ports`) in
    # which case new firewall rules permitting public traffic to those ports
    # will be added.
    vpc_name: skypilot-vpc

    # Should instances be assigned private IPs only? (optional)
    #
    # Set to true to use private IPs to communicate between the local client and
    # any SkyPilot nodes. This requires the networking stack be properly set up.
    #
    # This flag is typically set together with 'vpc_name' above and
    # 'ssh_proxy_command' below.
    #
    # Default: false.
    use_internal_ips: true
    # SSH proxy command (optional).
    #
    # Please refer to the aws.ssh_proxy_command section above for more details.
    ### Format 1 ###
    # A string; the same proxy command is used for all regions.
    ssh_proxy_command: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no gcpuser@<jump server public ip>
    ### Format 2 ###
    # A dict mapping region names to region-specific proxy commands.
    # NOTE: This restricts SkyPilot's search space for this cloud to only use
    # the specified regions and not any other regions in this cloud.
    ssh_proxy_command:
      us-central1: ssh -W %h:%p -p 1234 -o StrictHostKeyChecking=no myself@my.us-central1.proxy
      us-west1: ssh -W %h:%p -i ~/.ssh/sky-key -o StrictHostKeyChecking=no gcpuser@<jump server public ip>


    # Reserved capacity (optional).
    #
    # Whether to prioritize reserved instance types/locations (considered as 0
    # cost) in the optimizer.
    #
    # If you have "automatically consumed" reservations in your GCP project:
    # Setting this to true guarantees the optimizer will pick any matching
    # reservation and GCP will auto consume your reservation, and setting to
    # false means optimizer uses regular, non-zero pricing in optimization (if
    # by chance any matching reservation is selected, GCP still auto consumes
    # the reservation).
    #
    # If you have "specifically targeted" reservations (set by the
    # `specific_reservations` field below): This field will automatically be set
    # to true.
    #
    # Default: false.
    prioritize_reservations: false
    #
    # The "specifically targeted" reservations to be considered when provisioning
    # clusters on GCP. SkyPilot will automatically prioritize this reserved
    # capacity (considered as zero cost) if the requested resources matches the
    # reservation.
    #
    # Ref: https://cloud.google.com/compute/docs/instances/reservations-overview#consumption-type
    specific_reservations:
      - projects/my-project/reservations/my-reservation1
      - projects/my-project/reservations/my-reservation2


    # Identity to use for all GCP instances (optional).
    #
    # LOCAL_CREDENTIALS: The user's local credential files will be uploaded to
    # GCP instances created by SkyPilot. They are used for accessing cloud
    # resources (e.g., private buckets) or launching new instances (e.g., for
    # jobs/serve controllers).
    #
    # SERVICE_ACCOUNT: Local credential files are not uploaded to GCP
    # instances. SkyPilot will auto-create and reuse a service account for GCP
    # instances.
    #
    # Two caveats of SERVICE_ACCOUNT for multicloud users:
    #
    # - This only affects GCP instances. Local GCP credentials will still be
    #   uploaded to non-GCP instances (since those instances may need to access
    #   GCP resources).
    # - If the SkyPilot jobs/serve controller is on GCP, this setting will make
    #   non-GCP managed jobs / non-GCP service replicas fail to access any
    #   resources on GCP (since the controllers don't have GCP credential
    #   files to assign to these non-GCP instances).
    #
    # Default: 'LOCAL_CREDENTIALS'.
    remote_identity: LOCAL_CREDENTIALS

  # Advanced Kubernetes configurations (optional).
  kubernetes:
    # The networking mode for accessing SSH jump pod (optional).
    #
    # This must be either: 'nodeport' or 'portforward'. If not specified,
    # defaults to 'portforward'.
    #
    # nodeport: Exposes the jump pod SSH service on a static port number on each
    # Node, allowing external access to using <NodeIP>:<NodePort>. Using this
    # mode requires opening multiple ports on nodes in the Kubernetes cluster.
    #
    # portforward: Uses `kubectl port-forward` to create a tunnel and directly
    # access the jump pod SSH service in the Kubernetes cluster. Does not
    # require opening ports the cluster nodes and is more secure. 'portforward'
    # is used as default if 'networking' is not specified.
    networking: portforward

    # The mode to use for opening ports on Kubernetes
    #
    # This must be either: 'loadbalancer', 'ingress' or 'podip'.
    #
    # loadbalancer: Creates services of type `LoadBalancer` to expose ports.
    # See https://skypilot.readthedocs.io/en/latest/reference/kubernetes/kubernetes-setup.html#loadbalancer-service.
    # This mode is supported out of the box on most cloud managed Kubernetes
    # environments (e.g., GKE, EKS).
    #
    # ingress: Creates an ingress and a ClusterIP service for each port opened.
    # Requires an Nginx ingress controller to be configured on the Kubernetes cluster.
    # Refer to https://skypilot.readthedocs.io/en/latest/reference/kubernetes/kubernetes-setup.html#nginx-ingress
    # for details on deploying the NGINX ingress controller.
    #
    # podip: Directly returns the IP address of the pod. This mode does not
    # create any Kubernetes services and is a lightweight way to expose ports.
    # NOTE - ports exposed with podip mode are not accessible from outside the
    # Kubernetes cluster. This mode is useful for hosting internal services
    # that need to be accessed only by other pods in the same cluster.
    #
    # Default: loadbalancer
    ports: loadbalancer

    # Identity to use for all Kubernetes pods (optional).
    #
    # LOCAL_CREDENTIALS: The user's local ~/.kube/config will be uploaded to the
    # Kubernetes pods created by SkyPilot. They are used for authenticating with
    # the Kubernetes API server and launching new pods (e.g., for
    # spot/serve controllers).
    #
    # SERVICE_ACCOUNT: Local ~/.kube/config is not uploaded to Kubernetes pods.
    # SkyPilot will auto-create and reuse a service account with necessary roles
    # in the user's namespace.
    #
    # <string>: The name of a service account to use for all Kubernetes pods.
    # This service account must exist in the user's namespace and have all
    # necessary permissions. Refer to https://skypilot.readthedocs.io/en/latest/cloud-setup/cloud-permissions/kubernetes.html
    # for details on the roles required by the service account.
    #
    # Using SERVICE_ACCOUNT or a custom service account only affects Kubernetes
    # instances. Local ~/.kube/config will still be uploaded to non-Kubernetes
    # instances (e.g., a serve controller on GCP or AWS may need to provision
    # Kubernetes resources).
    #
    # Default: 'SERVICE_ACCOUNT'.
    remote_identity: my-k8s-service-account

    # Attach custom metadata to Kubernetes objects created by SkyPilot
    #
    # Uses the same schema as Kubernetes metadata object: https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.26/#objectmeta-v1-meta
    #
    # Since metadata is applied to all all objects created by SkyPilot,
    # specifying 'name' and 'namespace' fields here is not allowed.
    custom_metadata:
      labels:
        mylabel: myvalue
      annotations:
        myannotation: myvalue

    # Timeout for provisioning a pod (in seconds, optional)
    #
    # This timeout determines how long SkyPilot will wait for a pod in PENDING
    # status before giving up, deleting the pending pod and failing over to the
    # next cloud. Larger timeouts may be required for autoscaling clusters,
    # since the autoscaler may take some time to provision new nodes.
    # For example, an autoscaling CPU node pool on GKE may take upto 5 minutes
    # (300 seconds) to provision a new node.
    #
    # Note that this timeout includes time taken by the Kubernetes scheduler
    # itself, which can be upto 2-3 seconds.
    #
    # Can be set to -1 to wait indefinitely for pod provisioning (e.g., in
    # autoscaling clusters or clusters with queuing/admission control).
    #
    # Default: 10 seconds
    provision_timeout: 10

    # Autoscaler configured in the Kubernetes cluster (optional)
    #
    # This field informs SkyPilot about the cluster autoscaler used in the
    # Kubernetes cluster. Setting this field disables pre-launch checks for
    # GPU capacity in the cluster and SkyPilot relies on the autoscaler to
    # provision nodes with the required GPU capacity.
    #
    # Remember to set provision_timeout accordingly when using an autoscaler.
    #
    # Supported values: gke, karpenter, generic
    #   gke: uses cloud.google.com/gke-accelerator label to identify GPUs on nodes
    #   karpenter: uses karpenter.k8s.aws/instance-gpu-name label to identify GPUs on nodes
    #   generic: uses skypilot.co/accelerator labels to identify GPUs on nodes
    # Refer to https://skypilot.readthedocs.io/en/latest/reference/kubernetes/kubernetes-setup.html#setting-up-gpu-support
    # for more details on setting up labels for GPU support.
    #
    # Default: null (no autoscaler, autodetect label format for GPU nodes)
    autoscaler: gke

    # Additional fields to override the pod fields used by SkyPilot (optional)
    #
    # Any key:value pairs added here would get added to the pod spec used to
    # create SkyPilot pods. The schema follows the same schema for a Pod object
    # in the Kubernetes API:
    # https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.26/#pod-v1-core
    #
    # Some example use cases are shown below. All fields are optional.
    pod_config:
      metadata:
        labels:
          my-label: my-value    # Custom labels to SkyPilot pods
      spec:
        runtimeClassName: nvidia    # Custom runtimeClassName for GPU pods.
        imagePullSecrets:
          - name: my-secret     # Pull images from a private registry using a secret
        containers:
          - env:                # Custom environment variables for the pod, e.g., for proxy
            - name: HTTP_PROXY
              value: http://proxy-host:3128
            volumeMounts:       # Custom volume mounts for the pod
              - mountPath: /foo
                name: example-volume
                readOnly: true
        volumes:
          - name: example-volume
            hostPath:
              path: /tmp
              type: Directory
          - name: dshm          # Use this to modify the /dev/shm volume mounted by SkyPilot
            emptyDir:
              medium: Memory
              sizeLimit: 3Gi    # Set a size limit for the /dev/shm volume

  # Advanced OCI configurations (optional).
  oci:
    # A dict mapping region names to region-specific configurations, or
    # `default` for the default configuration.
    default:
      # The OCID of the profile to use for launching instances (optional).
      oci_config_profile: DEFAULT
      # The OCID of the compartment to use for launching instances (optional).
      compartment_ocid: ocid1.compartment.oc1..aaaaaaaahr7aicqtodxmcfor6pbqn3hvsngpftozyxzqw36gj4kh3w3kkj4q
      # The image tag to use for launching general instances (optional).
      image_tag_general: skypilot:cpu-ubuntu-2004
      # The image tag to use for launching GPU instances (optional).
      image_tag_gpu: skypilot:gpu-ubuntu-2004

    ap-seoul-1:
      # The OCID of the subnet to use for instances (optional).
      vcn_subnet: ocid1.subnet.oc1.ap-seoul-1.aaaaaaaa5c6wndifsij6yfyfehmi3tazn6mvhhiewqmajzcrlryurnl7nuja

    us-ashburn-1:
      vcn_subnet: ocid1.subnet.oc1.iad.aaaaaaaafbj7i3aqc4ofjaapa5edakde6g4ea2yaslcsay32cthp7qo55pxa


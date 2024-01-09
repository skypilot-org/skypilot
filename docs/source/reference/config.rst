.. _config-yaml:

Advanced Configurations
===========================

You can pass **optional configurations** to SkyPilot in the ``~/.sky/config.yaml`` file.

Such configurations apply to all new clusters and do not affect existing clusters.

Spec: ``~/.sky/config.yaml``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Available fields and semantics:

.. code-block:: yaml

  # Custom spot controller resources (optional).
  #
  # These take effects only when a spot controller does not already exist.
  #
  # Ref: https://skypilot.readthedocs.io/en/latest/examples/spot-jobs.html#customizing-spot-controller-resources
  spot:
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
    instance_tags:
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

  # Advanced GCP configurations (optional).
  # Apply to all new instances but not existing ones.
  gcp:
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
    # The specific reservation to be considered when provisioning clusters on GCP.
    # SkyPilot will automatically prioritize this reserved capacity (considered as
    # zero cost) if the requested resources matches the reservation.
    # Ref: https://cloud.google.com/compute/docs/instances/reservations-overview#consumption-type
    specific_reservations:
      - projects/my-project/reservations/my-reservation1
      - projects/my-project/reservations/my-reservation2

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


"""Constants used by the GCP provisioner."""

VERSION = 'v1'
# Using v2 according to
# https://cloud.google.com/tpu/docs/managing-tpus-tpu-vm#create-curl # pylint: disable=line-too-long
TPU_VM_VERSION = 'v2'

RAY = 'ray-autoscaler'
DEFAULT_SERVICE_ACCOUNT_ID = RAY + '-sa-' + VERSION
SERVICE_ACCOUNT_EMAIL_TEMPLATE = (
    '{account_id}@{project_id}.iam.gserviceaccount.com')
DEFAULT_SERVICE_ACCOUNT_CONFIG = {
    'displayName': f'Ray Autoscaler Service Account ({VERSION})',
}

SKYPILOT = 'skypilot'
SKYPILOT_SERVICE_ACCOUNT_ID = SKYPILOT + '-' + VERSION
SKYPILOT_SERVICE_ACCOUNT_EMAIL_TEMPLATE = (
    '{account_id}@{project_id}.iam.gserviceaccount.com')
SKYPILOT_SERVICE_ACCOUNT_CONFIG = {
    'displayName': f'SkyPilot Service Account ({VERSION})',
}

# Those roles will be always added.
# NOTE: `serviceAccountUser` allows the head node to create workers with
# a serviceAccount. `roleViewer` allows the head node to run bootstrap_gcp.
DEFAULT_SERVICE_ACCOUNT_ROLES = [
    'roles/storage.admin',
    'roles/compute.admin',
    'roles/iam.serviceAccountUser',
    'roles/iam.roleViewer',
]
# Those roles will only be added if there are TPU nodes defined in config.
TPU_SERVICE_ACCOUNT_ROLES = ['roles/tpu.admin']

# If there are TPU nodes in config, this field will be set
# to True in config['provider'].
HAS_TPU_PROVIDER_FIELD = '_has_tpus'

# NOTE: iam.serviceAccountUser allows the Head Node to create worker nodes
# with ServiceAccounts.

SKYPILOT_VPC_NAME = 'skypilot-vpc'

# Below parameters are from the default VPC on GCP.
# https://cloud.google.com/vpc/docs/firewalls#more_rules_default_vpc
VPC_TEMPLATE: dict = {
    'name': '{VPC_NAME}',
    'selfLink': 'projects/{PROJ_ID}/global/networks/{VPC_NAME}',
    'autoCreateSubnetworks': True,
    'mtu': 1460,
    'routingConfig': {
        'routingMode': 'GLOBAL'
    },
}
# Required firewall rules for SkyPilot to work.
FIREWALL_RULES_REQUIRED = [
    # Allow internal connections between GCP VMs for Ray multi-node cluster.
    {
        'direction': 'INGRESS',
        'allowed': [
            {
                'IPProtocol': 'tcp',
                'ports': ['0-65535']
            },
            {
                'IPProtocol': 'udp',
                'ports': ['0-65535']
            },
        ],
        'sourceRanges': ['10.128.0.0/9'],
    },
    # Allow ssh connection from anywhere.
    {
        'direction': 'INGRESS',
        'allowed': [{
            'IPProtocol': 'tcp',
            'ports': ['22'],
        }],
        # TODO(skypilot): some users reported that this should be relaxed (e.g.,
        # allowlisting only certain IPs to have ssh access).
        'sourceRanges': ['0.0.0.0/0'],
    },
]

# Template when creating firewall rules for a new VPC.
FIREWALL_RULES_TEMPLATE = [
    {
        'name': '{VPC_NAME}-allow-custom',
        'description': ('Allows connection from any source to any instance on '
                        'the network using custom protocols.'),
        'network': 'projects/{PROJ_ID}/global/networks/{VPC_NAME}',
        'selfLink':
            ('projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-custom'),
        'direction': 'INGRESS',
        'priority': 65534,
        'allowed': [
            {
                'IPProtocol': 'tcp',
                'ports': ['0-65535']
            },
            {
                'IPProtocol': 'udp',
                'ports': ['0-65535']
            },
            {
                'IPProtocol': 'icmp'
            },
        ],
        'sourceRanges': ['10.128.0.0/9'],
    },
    {
        'name': '{VPC_NAME}-allow-ssh',
        'description':
            ('Allows TCP connections from any source to any instance on the '
             'network using port 22.'),
        'network': 'projects/{PROJ_ID}/global/networks/{VPC_NAME}',
        'selfLink': 'projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-ssh',
        'direction': 'INGRESS',
        'priority': 65534,
        'allowed': [{
            'IPProtocol': 'tcp',
            'ports': ['22'],
        }],
        # TODO(skypilot): some users reported that this should be relaxed (e.g.,
        # allowlisting only certain IPs to have ssh access).
        'sourceRanges': ['0.0.0.0/0'],
    },
    {
        'name': '{VPC_NAME}-allow-icmp',
        'description': ('Allows ICMP connections from any source to any '
                        'instance on the network.'),
        'network': 'projects/{PROJ_ID}/global/networks/{VPC_NAME}',
        'selfLink': 'projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-icmp',
        'direction': 'INGRESS',
        'priority': 65534,
        'allowed': [{
            'IPProtocol': 'icmp',
        }],
        'sourceRanges': ['0.0.0.0/0'],
    },
]

# A list of permissions required to run SkyPilot on GCP.
# Keep this in sync with https://skypilot.readthedocs.io/en/latest/cloud-setup/cloud-permissions/gcp.html # pylint: disable=line-too-long
VM_MINIMAL_PERMISSIONS = [
    'compute.disks.create',
    'compute.disks.list',
    'compute.firewalls.get',
    'compute.instances.create',
    'compute.instances.delete',
    'compute.instances.get',
    'compute.instances.list',
    'compute.instances.setLabels',
    'compute.instances.setServiceAccount',
    'compute.instances.start',
    'compute.instances.stop',
    'compute.networks.get',
    'compute.networks.list',
    'compute.networks.getEffectiveFirewalls',
    'compute.globalOperations.get',
    'compute.subnetworks.use',
    'compute.subnetworks.list',
    'compute.subnetworks.useExternalIp',
    'compute.projects.get',
    'compute.zoneOperations.get',
    'iam.roles.get',
    # We now skip the check for `iam.serviceAccounts.actAs` permission for
    # simplicity as it can be granted at the service-account level.
    # Check: sky.provision.gcp.config::_is_permission_satisfied
    # 'iam.serviceAccounts.actAs',
    'iam.serviceAccounts.get',
    'serviceusage.services.enable',
    'serviceusage.services.list',
    'serviceusage.services.use',
    'resourcemanager.projects.get',
    'resourcemanager.projects.getIamPolicy',
]

# Permissions implied by GCP built-in roles. We hardcode these here, as we
# cannot get the permissions of built-in role from the GCP Python API.
# The lists are not exhaustive, but should cover the permissions listed in
# VM_MINIMAL_PERMISSIONS.
# Check: sky.provision.gcp.config::_is_permission_satisfied
BUILTIN_ROLE_TO_PERMISSIONS = {
    'roles/iam.serviceAccountUser': ['iam.serviceAccounts.actAs'],
    'roles/iam.serviceAccountViewer': [
        'iam.serviceAccounts.get', 'iam.serviceAccounts.getIamPolicy'
    ],
    # TODO(zhwu): Add more built-in roles to make the permission check more
    # robust.
}

FIREWALL_PERMISSIONS = [
    'compute.firewalls.create',
    'compute.firewalls.delete',
]

RESERVATION_PERMISSIONS = [
    'compute.reservations.list',
]

TPU_MINIMAL_PERMISSIONS = [
    'tpu.nodes.create',
    'tpu.nodes.delete',
    'tpu.nodes.list',
    'tpu.nodes.get',
    'tpu.nodes.update',
    'tpu.operations.get',
]

# The maximum number of times to poll for the status of an operation.
POLL_INTERVAL = 1
MAX_POLLS = 60 // POLL_INTERVAL
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_STOP = MAX_POLLS * 8

# MIG constants
MANAGED_INSTANCE_GROUP_CONFIG = 'managed-instance-group'
DEFAULT_MANAGED_INSTANCE_GROUP_PROVISION_TIMEOUT = 900  # 15 minutes
MIG_NAME_PREFIX = 'sky-mig-'
INSTANCE_TEMPLATE_NAME_PREFIX = 'sky-it-'

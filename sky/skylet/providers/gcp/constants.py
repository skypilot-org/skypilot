SKYPILOT_VPC_NAME = "skypilot-vpc"

# Below parameters are from the default VPC on GCP.
# https://cloud.google.com/vpc/docs/firewalls#more_rules_default_vpc
VPC_TEMPLATE = {
    "name": "{VPC_NAME}",
    "selfLink": "projects/{PROJ_ID}/global/networks/{VPC_NAME}",
    "autoCreateSubnetworks": True,
    "mtu": 1460,
    "routingConfig": {"routingMode": "GLOBAL"},
}
# Required firewall rules for SkyPilot to work.
FIREWALL_RULES_REQUIRED = [
    # Allow internal connections between GCP VMs for Ray multi-node cluster.
    {
        "direction": "INGRESS",
        "allowed": [
            {"IPProtocol": "tcp", "ports": ["0-65535"]},
            {"IPProtocol": "udp", "ports": ["0-65535"]},
        ],
        "sourceRanges": ["10.128.0.0/9"],
    },
    # Allow ssh connection from anywhere.
    {
        "direction": "INGRESS",
        "allowed": [
            {
                "IPProtocol": "tcp",
                "ports": ["22"],
            }
        ],
        "sourceRanges": ["0.0.0.0/0"],
    },
]
# Template when creating firewall rules for a new VPC.
FIREWALL_RULES_TEMPLATE = [
    {
        "name": "{VPC_NAME}-allow-custom",
        "description": "Allows connection from any source to any instance on the network using custom protocols.",
        "network": "projects/{PROJ_ID}/global/networks/{VPC_NAME}",
        "selfLink": "projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-custom",
        "direction": "INGRESS",
        "priority": 65534,
        "allowed": [
            {"IPProtocol": "tcp", "ports": ["0-65535"]},
            {"IPProtocol": "udp", "ports": ["0-65535"]},
            {"IPProtocol": "icmp"},
        ],
        "sourceRanges": ["10.128.0.0/9"],
    },
    {
        "name": "{VPC_NAME}-allow-ssh",
        "description": "Allows TCP connections from any source to any instance on the network using port 22.",
        "network": "projects/{PROJ_ID}/global/networks/{VPC_NAME}",
        "selfLink": "projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-ssh",
        "direction": "INGRESS",
        "priority": 65534,
        "allowed": [
            {
                "IPProtocol": "tcp",
                "ports": ["22"],
            }
        ],
        "sourceRanges": ["0.0.0.0/0"],
    },
    {
        "name": "{VPC_NAME}-allow-icmp",
        "description": "Allows ICMP connections from any source to any instance on the network.",
        "network": "projects/{PROJ_ID}/global/networks/{VPC_NAME}",
        "selfLink": "projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-icmp",
        "direction": "INGRESS",
        "priority": 65534,
        "allowed": [
            {
                "IPProtocol": "icmp",
            }
        ],
        "sourceRanges": ["0.0.0.0/0"],
    },
]

# A list of permissions required to run SkyPilot on GCP.
# Keep this in sync with https://skypilot.readthedocs.io/en/latest/cloud-setup/cloud-permissions.html#gcp # pylint: disable=line-too-long
VM_MINIMAL_PERMISSIONS = [
    "compute.disks.create",
    "compute.disks.list",
    "compute.firewalls.create",
    "compute.firewalls.delete",
    "compute.firewalls.get",
    "compute.instances.create",
    "compute.instances.delete",
    "compute.instances.get",
    "compute.instances.list",
    "compute.instances.setLabels",
    "compute.instances.setServiceAccount",
    "compute.instances.start",
    "compute.instances.stop",
    "compute.networks.get",
    "compute.networks.list",
    "compute.networks.getEffectiveFirewalls",
    "compute.globalOperations.get",
    "compute.subnetworks.use",
    "compute.subnetworks.list",
    "compute.subnetworks.useExternalIp",
    "compute.projects.get",
    "compute.zoneOperations.get",
    "iam.roles.get",
    "iam.serviceAccounts.actAs",
    "iam.serviceAccounts.get",
    "serviceusage.services.enable",
    "serviceusage.services.list",
    "serviceusage.services.use",
    "resourcemanager.projects.get",
    "resourcemanager.projects.getIamPolicy",
]

TPU_MINIMAL_PERMISSIONS = [
    "tpu.nodes.create",
    "tpu.nodes.delete",
    "tpu.nodes.list",
    "tpu.nodes.get",
    "tpu.nodes.update",
    "tpu.operations.get",
]

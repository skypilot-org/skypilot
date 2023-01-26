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
FIREWALL_RULES_TEMPLATE = [
    {
        "name": "{VPC_NAME}-allow-custom",
        "description": "Allows connection from any source to any instance on the network using custom protocols.",
        "network": "projects/{PROJ_ID}/global/networks/{VPC_NAME}",
        "selfLink": "projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-custom",
        "direction": "INGRESS",
        "priority": 65534,
        "allowed": [
            {'IPProtocol': 'tcp', 'ports': ['0-65535']},
            {'IPProtocol': 'udp', 'ports': ['0-65535']},
            {'IPProtocol': 'icmp'}
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
        "allowed": [{
            "IPProtocol": "tcp",
            "ports": ["22"],
        }],
        "sourceRanges": ["0.0.0.0/0"],
    },
    {
        "name": "{VPC_NAME}-allow-icmp",
        "description": "Allows ICMP connections from any source to any instance on the network.",
        "network": "projects/{PROJ_ID}/global/networks/{VPC_NAME}",
        "selfLink": "projects/{PROJ_ID}/global/firewalls/{VPC_NAME}-allow-icmp",
        "direction": "INGRESS",
        "priority": 65534,
        "allowed": [{
            "IPProtocol": "icmp",
        }],
        "sourceRanges": ["0.0.0.0/0"],
    },
]

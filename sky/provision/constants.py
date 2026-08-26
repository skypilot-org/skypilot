"""Constants used in the SkyPilot provisioner."""

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
# Legacy tag for backward compatibility to distinguish head and worker nodes.
TAG_RAY_NODE_KIND = 'ray-node-type'
TAG_SKYPILOT_HEAD_NODE = 'skypilot-head-node'

HEAD_NODE_TAGS = {
    TAG_RAY_NODE_KIND: 'head',
    TAG_SKYPILOT_HEAD_NODE: '1',
}

WORKER_NODE_TAGS = {
    TAG_RAY_NODE_KIND: 'worker',
    TAG_SKYPILOT_HEAD_NODE: '0',
}

# Magic error string indicating that no nodes were launched.
ERROR_NO_NODES_LAUNCHED = 'SKYPILOT_ERROR_NO_NODES_LAUNCHED'

# Minimum advertised public-internet bandwidth required for ``network_tier:
# best`` on marketplace clouds that expose bandwidth constraints. This does
# not request an accelerated private interconnect such as EFA or InfiniBand.
MARKETPLACE_BEST_NETWORK_MIN_BANDWIDTH_MBPS = 1000

# Clouds whose provisioner starts Ray asynchronously inside the provisioned
# pod (via kubernetes-ray.yml.j2) and uses kubectl rather than SSH to run
# commands on the pod. SSH node pools run on k3s under the hood, so they
# share the same provisioning flow as the kubernetes cloud.
K8S_BASED_CLOUDS = ['kubernetes', 'ssh']

# Names for Azure Deployments.
DEPLOYMENT_NAME = 'skypilot-config'
LEGACY_DEPLOYMENT_NAME = 'ray-config'
EXTERNAL_RG_BOOTSTRAP_DEPLOYMENT_NAME = (
    'skypilot-bootstrap-{cluster_name_on_cloud}')
EXTERNAL_RG_VM_DEPLOYMENT_NAME = 'skypilot-vm-{cluster_name_on_cloud}'

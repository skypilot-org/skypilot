"""Constants for Kubernetes provisioning."""

# Canonical GPU names for GPU detection and labeling.
# Used by both GFDLabelFormatter and the GPU labeler script.
#
# IMPORTANT: Order matters for the GPU labeler script which uses substring
# matching (canonical_name in gpu_name). Names that are prefixes of other
# names must come later (e.g., 'L40S' before 'L40' before 'L4') to prevent
# 'L4' from matching 'L40S'. GFDLabelFormatter uses word boundary regex
# and is order-independent.
CANONICAL_GPU_NAMES = [
    # Blackwell architecture (2024+)
    'GB300',
    'GB200',
    'B300',
    'B200',
    'B100',
    # Hopper architecture
    'GH200',
    'H200',
    'H100-80GB',
    'H100-MEGA',
    'H100',
    # Ampere architecture
    'A100-80GB',
    'A100',
    'A10G',
    'A10',
    'A16',
    'A30',
    'A40',
    # Ada Lovelace architecture - Professional (RTX Ada)
    'RTX6000-Ada',
    'L40S',
    'L40',
    'L4',
    # Quadro/RTX Professional (Ampere)
    'A6000',
    'A5000',
    'A4000',
    # Older architectures - Volta/Pascal/Turing
    'V100',
    'P100',
    'P40',
    'P4000',
    'P4',
    'T4g',
    'T4',
    'K80',
    'M60',
]

NO_GPU_HELP_MESSAGE = ('If your cluster contains GPUs, make sure '
                       'nvidia.com/gpu resource is available on the nodes and '
                       'the node labels for identifying GPUs '
                       '(e.g., skypilot.co/accelerator) are setup correctly. ')

KUBERNETES_IN_CLUSTER_NAMESPACE_ENV_VAR = 'SKYPILOT_IN_CLUSTER_NAMESPACE'

# Name of kubernetes exec auth wrapper script
SKY_K8S_EXEC_AUTH_WRAPPER = 'sky-kube-exec-wrapper'

# PATH envvar for kubectl exec auth execve
SKY_K8S_EXEC_AUTH_PATH = '$HOME/skypilot-runtime/bin:$HOME/google-cloud-sdk/bin:$PATH'  # pylint: disable=line-too-long

# cache directory for kubeconfig with modified exec auth
SKY_K8S_EXEC_AUTH_KUBECONFIG_CACHE = '~/.sky/generated/kubeconfigs'

# Labels for the Pods created by SkyPilot
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_POD_INITIALIZED = 'skypilot-initialized'
TAG_SKYPILOT_DEPLOYMENT_NAME = 'skypilot-deployment-name'

# Default name of the primary workload container in SkyPilot Ray pods.
RAY_NODE_CONTAINER_NAME = 'ray-node'

# Pod phases that are not holding PVCs
PVC_NOT_HOLD_POD_PHASES = ['Succeeded', 'Failed']

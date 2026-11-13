"""Constants for Kubernetes provisioning."""

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

# Finalizer Kueue puts on pod-group pods; retained on a terminating pod until a
# replacement is observed, so it blocks garbage-collection of a deleted pod.
KUEUE_MANAGED_FINALIZER = 'kueue.x-k8s.io/managed'

# Label SkyPilot stamps on a cluster's pods when (and only when) a Kueue queue
# is configured (templates/kubernetes-ray.yml.j2).
KUEUE_QUEUE_NAME_LABEL = 'kueue.x-k8s.io/queue-name'

# Provider-config key recording the Kubernetes context a cluster's pods
# actually run on, when that can differ from the context the cluster was
# submitted to (see get_execution_context_from_config). Recorded alongside
# `provider.context` rather than replacing it: teardown, status and resource
# cleanup all read `context` and must keep addressing the submitting cluster.
PROVIDER_EXECUTION_CONTEXT_KEY = 'execution_context'

# Pod phases that are not holding PVCs
PVC_NOT_HOLD_POD_PHASES = ['Succeeded', 'Failed']

# Ray ``--num-cpus`` for a dedicated thin head (integer; ``ray start`` rejects
# fractions). ``0`` means no Ray CPU tasks schedule on the head. Setup pins via
# ``node:<ip>`` resources instead of a CPU placement-group bundle. Pod
# requests/limits still come from ``head_node_*``.
THIN_HEAD_RAY_NUM_CPUS = '0'

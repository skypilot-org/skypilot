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

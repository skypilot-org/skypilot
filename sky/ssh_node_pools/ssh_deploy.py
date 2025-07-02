"""SSH-based Kubernetes Cluster Deployment"""
# This is the python native function call method of creating an SSH Node Pool

import os
from typing import Optional, Tuple

from sky.ssh_node_pools import ssh_deploy_utils

DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')


def deploy_cluster(cleanup: bool=False,
                   infra: Optional[str]=None,
                   kubeconfig_path: Optional[str]=None) -> Tuple[bool, str]:
    """Deploy a Kubernetes cluster on SSH targets.

    This function deploys a Kubernetes (k3s) cluster on the specified machines.

    Args:
        cleanup: Whether to clean up the cluster instead of deploying.
        infra: Name of the cluster to use. If None, all clusters will be used.
            Must not be None for deployment
        kubeconfig_path: Path to save the Kubernetes configuration file.
            If None, the default ~/.kube/config will be used.
    """
    assert cleanup or infra is not None

    # check requirements
    ssh_deploy_utils.check_ssh_cluster_dependencies()

    # get kubeconfig paths
    if not kubeconfig_path:
        kubeconfig_path = DEFAULT_KUBECONFIG_PATH
    else:
        kubeconfig_path = os.path.expanduser(kubeconfig_path)

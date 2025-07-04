"""SSH-based Kubernetes Cluster Deployment"""
# This is the python native function call method of creating an SSH Node Pool
import os
from typing import Optional, Tuple

from sky import sky_logging
from sky.utils.kubernetes import kubernetes_deploy_utils

# Colors for nicer UX
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
WARNING_YELLOW = '\x1b[33m'
NC = '\033[0m'  # No color

DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
# Get the directory of this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

logger = sky_logging.init_logger(__name__)


def deploy_cluster(cleanup: bool = False,
                   infra: Optional[str] = None,
                   kubeconfig_path: Optional[str] = None) -> Tuple[bool, str]:
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
    kubernetes_deploy_utils.check_ssh_cluster_dependencies()

    # get kubeconfig paths
    if not kubeconfig_path:
        kubeconfig_path = DEFAULT_KUBECONFIG_PATH
    else:
        kubeconfig_path = os.path.expanduser(kubeconfig_path)

    return True, ''

"""Module for Deploying SSH Node Pools"""
from typing import Optional

import colorama

from sky import sky_logging
from sky.ssh_node_pools.deploy import deploy_ssh_node_pools
from sky.ssh_node_pools.deploy import utils as deploy_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def deploy_ssh_cluster(cleanup: bool = False,
                       infra: Optional[str] = None,
                       kubeconfig_path: Optional[str] = None):
    """Deploy a Kubernetes cluster on SSH targets.

    This function reads ~/.sky/ssh_node_pools.yaml and uses it to deploy a
    Kubernetes cluster on the specified machines.

    Args:
        cleanup: Whether to clean up the cluster instead of deploying.
        infra: Name of the cluster in ssh_node_pools.yaml to use.
            If None, the first cluster in the file will be used.
        kubeconfig_path: Path to save the Kubernetes configuration file.
            If None, the default ~/.kube/config will be used.
    """
    deploy_utils.check_ssh_cluster_dependencies()

    action = 'Cleanup' if cleanup else 'Deployment'
    msg_str = f'Initializing SSH Node Pools {action}...'

    with rich_utils.safe_status(ux_utils.spinner_message(msg_str)):
        try:
            deploy_ssh_node_pools.deploy_clusters(
                infra=infra, cleanup=cleanup, kubeconfig_path=kubeconfig_path)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(str(e))
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Failed to deploy SkyPilot on some Node Pools.') from e

    logger.info('')
    if cleanup:
        logger.info(
            ux_utils.finishing_message(
                '🎉 SSH Node Pools cleaned up successfully.'))
    else:
        logger.info(
            ux_utils.finishing_message(
                '🎉 SSH Node Pools set up successfully. ',
                follow_up_message=(
                    f'Run `{colorama.Style.BRIGHT}'
                    f'sky check ssh'
                    f'{colorama.Style.RESET_ALL}` to verify access, '
                    f'`{colorama.Style.BRIGHT}sky launch --infra ssh'
                    f'{colorama.Style.RESET_ALL}` to launch a cluster.')))

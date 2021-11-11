from kubernetes import config, client, utils
import logging
import os
import sky

logger = logging.getLogger(__name__)


def load_k8s_config(kubeconfig_path: str = ""):
    if kubeconfig_path:
        logger.debug(f'Using kubeconfig path: {kubeconfig_path}')
        config.load_kube_config(config_file=kubeconfig_path)
    elif os.getenv('KUBERNETES_SERVICE_HOST'):
        logger.debug('Detected running inside cluster. Using incluster auth.')
        config.load_incluster_config()
    else:
        logger.debug('Using kube auth.')
        config.load_kube_config()


def deploy_yaml(kubeconfig_path: str,
                yaml_path: str):
    """
    Deploys a YAML file according on the provided cluster
    """
    load_k8s_config(kubeconfig_path)
    k8s_client = client.ApiClient()
    utils.create_from_yaml(k8s_client, yaml_path)


def deploy_dashboard(kubeconfig_path: str):
    """
    Deploys the kubernetes dashboard
    """
    sky_root = os.path.dirname(sky.__file__)
    yaml_file = os.path.join(sky_root, 'k8s/k8s_yamls/dashboard.yaml')
    deploy_yaml(kubeconfig_path, yaml_file)
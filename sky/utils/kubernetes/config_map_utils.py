"""Utilities for Kubernetes ConfigMap operations in SkyPilot."""
import os

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import kubernetes
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# Kubernetes ConfigMap sync constants
_KUBE_SERVICE_ACCOUNT_PATH = '/var/run/secrets/kubernetes.io/serviceaccount'
_CONFIGMAP_SYNC_TIMEOUT = 10  # seconds


def is_running_in_kubernetes() -> bool:
    """Check if we're running inside a Kubernetes pod."""
    return os.path.exists(f'{_KUBE_SERVICE_ACCOUNT_PATH}/token')


def _get_kubernetes_namespace() -> str:
    """Get the current Kubernetes namespace from the service account."""
    try:
        namespace_file = f'{_KUBE_SERVICE_ACCOUNT_PATH}/namespace'
        if os.path.exists(namespace_file):
            with open(namespace_file, encoding='utf-8') as f:
                return f.read().strip()
    except (OSError, IOError):
        pass
    return 'default'


def _get_configmap_name() -> str:
    """Get the ConfigMap name for the SkyPilot config."""
    release_name = (os.getenv('HELM_RELEASE_NAME') or
                    os.getenv('SKYPILOT_RELEASE_NAME') or 'skypilot')
    return f'{release_name}-config'


def initialize_configmap_sync_on_startup(config_file_path: str) -> None:
    """Initialize ConfigMap sync on API server startup.

    This syncs existing PVC config to ConfigMap if ConfigMap doesn't exist.
    This handles the upgrade scenario where an existing deployment has
    workspace configs on PVC but no ConfigMap exists.

    Args:
        config_file_path: Path to the config file to sync.
    """
    config_file_path = os.path.expanduser(config_file_path)
    if not is_running_in_kubernetes() or not os.path.exists(config_file_path):
        return

    try:
        namespace = _get_kubernetes_namespace()
        configmap_name = _get_configmap_name()

        # Check if ConfigMap exists
        try:
            kubernetes.core_api().read_namespaced_config_map(
                name=configmap_name, namespace=namespace)
            # ConfigMap exists, don't overwrite it
            logger.debug(f'ConfigMap {configmap_name} already exists')
            return
        except kubernetes.kubernetes.client.rest.ApiException as e:
            if e.status != 404:
                raise
            # ConfigMap doesn't exist, create it

        current_config = skypilot_config.parse_and_validate_config_file(
            config_file_path)
        config_yaml = yaml_utils.dump_yaml_str(dict(current_config))

        configmap_body = {
            'apiVersion': 'v1',
            'kind': 'ConfigMap',
            'metadata': {
                'name': configmap_name,
                'namespace': namespace,
                'labels': {
                    'app.kubernetes.io/name': 'skypilot',
                    'app.kubernetes.io/component': 'config'
                }
            },
            'data': {
                'config.yaml': config_yaml
            }
        }

        kubernetes.core_api().create_namespaced_config_map(
            namespace=namespace,
            body=configmap_body,
            _request_timeout=_CONFIGMAP_SYNC_TIMEOUT)

        logger.info(f'Synced PVC config to new ConfigMap {configmap_name}')

    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to initialize ConfigMap sync: {e}')


def patch_configmap_with_config(config, config_file_path: str) -> None:
    """Patch the Kubernetes ConfigMap with the updated config.

    Args:
        config: The updated config to sync to the ConfigMap.
        config_file_path: Path to the config file for fallback sync.
    """
    if not is_running_in_kubernetes():
        return

    try:
        namespace = _get_kubernetes_namespace()
        configmap_name = _get_configmap_name()
        config_yaml = yaml_utils.dump_yaml_str(dict(config))
        patch_body = {'data': {'config.yaml': config_yaml}}

        try:
            kubernetes.core_api().patch_namespaced_config_map(
                name=configmap_name,
                namespace=namespace,
                body=patch_body,
                _request_timeout=_CONFIGMAP_SYNC_TIMEOUT)
            logger.debug(f'Synced config to ConfigMap {configmap_name}')
        except kubernetes.kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                # ConfigMap doesn't exist, create it
                logger.info(f'ConfigMap {configmap_name} not found, creating')
                initialize_configmap_sync_on_startup(config_file_path)
            else:
                raise

    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Failed to sync config to ConfigMap: {e}')

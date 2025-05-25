"""Utilities for Kubernetes ConfigMap operations in SkyPilot."""
import os

from sky import sky_logging
from sky.adaptors import kubernetes
from sky.utils import common_utils
from sky.utils import config_utils

logger = sky_logging.init_logger(__name__)

# Kubernetes ConfigMap sync constants
_KUBE_SERVICE_ACCOUNT_PATH = '/var/run/secrets/kubernetes.io/serviceaccount'
_KUBE_NAMESPACE_FILE = f'{_KUBE_SERVICE_ACCOUNT_PATH}/namespace'
_CONFIGMAP_SYNC_TIMEOUT = 10  # seconds


def is_running_in_kubernetes() -> bool:
    """Check if we're running inside a Kubernetes pod.

    This is determined by checking for the existence of the service account
    token file that Kubernetes mounts into every pod.
    """
    return os.path.exists(f'{_KUBE_SERVICE_ACCOUNT_PATH}/token')


def get_kubernetes_namespace() -> str:
    """Get the current Kubernetes namespace from the service account.

    Returns:
        The namespace where the current pod is running, or 'default' if not
        available.
    """
    try:
        if os.path.exists(_KUBE_NAMESPACE_FILE):
            with open(_KUBE_NAMESPACE_FILE, encoding='utf-8') as f:
                return f.read().strip()
    except (OSError, IOError):
        pass
    return 'default'


def get_configmap_name() -> str:
    """Get the ConfigMap name for the SkyPilot config.

    The ConfigMap name is determined from environment variables set by the
    Helm chart deployment. Falls back to a default name if not available.
    """
    # Try to get the release name from common Helm environment variables
    release_name = (os.getenv('HELM_RELEASE_NAME') or
                    os.getenv('SKYPILOT_RELEASE_NAME') or 'skypilot')
    return f'{release_name}-config'


def sync_pvc_config_to_configmap(config_file_path: str) -> None:
    """Sync existing PVC config to ConfigMap when ConfigMap doesn't exist.

    This function handles the case where there's existing workspace
    configuration on the PVC but no ConfigMap exists. It creates/updates the
    ConfigMap with the current local config to establish the ConfigMap as the
    source of truth.

    Args:
        config_file_path: Path to the config file to sync.
    """
    if not is_running_in_kubernetes():
        return

    try:
        namespace = get_kubernetes_namespace()
        configmap_name = get_configmap_name()

        # Check if ConfigMap exists
        try:
            kubernetes.core_api().read_namespaced_config_map(
                name=configmap_name, namespace=namespace)
            # ConfigMap exists, don't overwrite it
            logger.debug(f'ConfigMap {configmap_name} already exists, '
                         'skipping PVC sync')
            return
        except kubernetes.kubernetes.client.rest.ApiException as e:
            if e.status != 404:
                # Some other error occurred
                raise
            # ConfigMap doesn't exist, proceed with sync

        # Read current local config
        if not os.path.exists(config_file_path):
            logger.debug('No local config file exists, skipping PVC sync')
            return

        # Parse and validate the config file
        # pylint: disable=import-outside-toplevel
        from sky import skypilot_config
        current_config = skypilot_config.parse_and_validate_config_file(
            config_file_path)
        config_yaml = common_utils.dump_yaml_str(dict(current_config))

        # Create ConfigMap with current config
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

        logger.info(f'Successfully synced PVC config to new ConfigMap '
                    f'{configmap_name} in namespace {namespace}')

    except ImportError:
        # Kubernetes client not available, skip silently
        logger.debug('Kubernetes client not available, skipping PVC sync')
    except Exception as e:  # pylint: disable=broad-except
        # Log the error but don't fail
        logger.warning(f'Failed to sync PVC config to ConfigMap: {e}. '
                       'Continuing with local config.')


def patch_configmap_with_config(config: config_utils.Config,
                                config_file_path: str) -> None:
    """Patch the Kubernetes ConfigMap with the updated config.

    This function updates the ConfigMap that was originally created by Helm
    to keep it in sync with local config changes, particularly workspace
    updates.

    Args:
        config: The updated config to sync to the ConfigMap.
        config_file_path: Path to the config file for fallback sync.
    """
    if not is_running_in_kubernetes():
        return

    try:
        namespace = get_kubernetes_namespace()
        configmap_name = get_configmap_name()

        # Convert config to YAML string
        config_yaml = common_utils.dump_yaml_str(dict(config))

        # Create the patch body
        patch_body = {'data': {'config.yaml': config_yaml}}

        try:
            # Patch the ConfigMap
            kubernetes.core_api().patch_namespaced_config_map(
                name=configmap_name,
                namespace=namespace,
                body=patch_body,
                _request_timeout=_CONFIGMAP_SYNC_TIMEOUT)

            logger.debug(f'Successfully synced config to ConfigMap '
                         f'{configmap_name} in namespace {namespace}')

        except kubernetes.kubernetes.client.rest.ApiException as e:
            if e.status == 404:
                # ConfigMap doesn't exist, sync PVC config to create it
                logger.info(f'ConfigMap {configmap_name} not found, '
                            'syncing current config to new ConfigMap')
                sync_pvc_config_to_configmap(config_file_path)
            else:
                raise

    except ImportError:
        # Kubernetes client not available, skip silently
        logger.debug('Kubernetes client not available, skipping ConfigMap sync')
    except Exception as e:  # pylint: disable=broad-except
        # Log the error but don't fail the config update
        logger.warning(
            f'Failed to sync config to Kubernetes ConfigMap: {e}. '
            'The local config has been updated successfully, but the '
            'ConfigMap may be out of sync. On pod restart, changes '
            'may be lost unless the ConfigMap is manually updated.')


def initialize_configmap_sync_on_startup(config_file_path: str) -> None:
    """Initialize ConfigMap sync on API server startup.

    This function should be called during API server startup to:
    1. Only sync existing PVC config to ConfigMap if ConfigMap doesn't exist
    2. Ensure ConfigMap becomes the source of truth going forward

    This handles the upgrade scenario where an existing deployment has
    workspace configs on PVC but no ConfigMap exists. It will NOT overwrite
    an existing ConfigMap that was provided via Helm values.

    Behavior:
    - If ConfigMap exists (provided via Helm): Do nothing, use the provided
      ConfigMap (guaranteed by Helm chart, see api-deployment.yaml)
    - If ConfigMap doesn't exist: Sync PVC config to create ConfigMap (if PVC
      config exists)

    Args:
        config_file_path: Path to the config file to sync.
    """
    if not is_running_in_kubernetes():
        return

    logger.debug('Initializing ConfigMap sync on startup')

    # Only proceed if there's a config file on PVC to sync
    if os.path.exists(config_file_path):
        try:
            # sync_pvc_config_to_configmap will check if ConfigMap exists
            # and only create it if it doesn't exist
            sync_pvc_config_to_configmap(config_file_path)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to initialize ConfigMap sync: {e}')
    else:
        logger.debug('No PVC config file exists, skipping ConfigMap sync initialization')

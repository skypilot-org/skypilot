"""Kubernetes adaptors"""

# pylint: disable=import-outside-toplevel

import os

from sky.adaptors import common
from sky.utils import env_options
from sky.utils import ux_utils

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Kubernetes. '
                         'Try running: pip install "skypilot[kubernetes]"')
kubernetes = common.LazyImport('kubernetes',
                               import_error_message=_IMPORT_ERROR_MESSAGE)
urllib3 = common.LazyImport('urllib3',
                            import_error_message=_IMPORT_ERROR_MESSAGE)

_configured = False
_core_api = None
_auth_api = None
_networking_api = None
_custom_objects_api = None
_node_api = None
_apps_api = None
_api_client = None

# Timeout to use for API calls
API_TIMEOUT = 5


def _load_config():
    global _configured
    if _configured:
        return
    try:
        # Load in-cluster config if running in a pod
        # Kubernetes set environment variables for service discovery do not
        # show up in SkyPilot tasks. For now, we work around by using
        # DNS name instead of environment variables.
        # See issue: https://github.com/skypilot-org/skypilot/issues/2287
        os.environ['KUBERNETES_SERVICE_HOST'] = 'kubernetes.default.svc'
        os.environ['KUBERNETES_SERVICE_PORT'] = '443'
        kubernetes.config.load_incluster_config()
    except kubernetes.config.config_exception.ConfigException:
        try:
            kubernetes.config.load_kube_config()
        except kubernetes.config.config_exception.ConfigException as e:
            suffix = ''
            if env_options.Options.SHOW_DEBUG_INFO.get():
                suffix += f' Error: {str(e)}'
            # Check if exception was due to no current-context
            if 'Expected key current-context' in str(e):
                err_str = ('Failed to load Kubernetes configuration. '
                           'Kubeconfig does not contain any valid context(s).'
                           f'{suffix}\n'
                           '    If you were running a local Kubernetes '
                           'cluster, run `sky local up` to start the cluster.')
            else:
                err_str = ('Failed to load Kubernetes configuration. '
                           'Please check if your kubeconfig file exists at '
                           f'~/.kube/config and is valid.{suffix}')
            err_str += '\nTo disable Kubernetes for SkyPilot: run `sky check`.'
            with ux_utils.print_exception_no_traceback():
                raise ValueError(err_str) from None
    _configured = True


def core_api():
    global _core_api
    if _core_api is None:
        _load_config()
        _core_api = kubernetes.client.CoreV1Api()

    return _core_api


def auth_api():
    global _auth_api
    if _auth_api is None:
        _load_config()
        _auth_api = kubernetes.client.RbacAuthorizationV1Api()

    return _auth_api


def networking_api():
    global _networking_api
    if _networking_api is None:
        _load_config()
        _networking_api = kubernetes.client.NetworkingV1Api()

    return _networking_api


def custom_objects_api():
    global _custom_objects_api
    if _custom_objects_api is None:
        _load_config()
        _custom_objects_api = kubernetes.client.CustomObjectsApi()

    return _custom_objects_api


def node_api():
    global _node_api
    if _node_api is None:
        _load_config()
        _node_api = kubernetes.client.NodeV1Api()

    return _node_api


def apps_api():
    global _apps_api
    if _apps_api is None:
        _load_config()
        _apps_api = kubernetes.client.AppsV1Api()

    return _apps_api


def api_client():
    global _api_client
    if _api_client is None:
        _load_config()
        _api_client = kubernetes.client.ApiClient()

    return _api_client


def api_exception():
    return kubernetes.client.rest.ApiException


def config_exception():
    return kubernetes.config.config_exception.ConfigException


def max_retry_error():
    return urllib3.exceptions.MaxRetryError


def stream():
    return kubernetes.stream.stream

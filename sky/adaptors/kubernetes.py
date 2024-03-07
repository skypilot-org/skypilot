"""Kubernetes adaptors"""

# pylint: disable=import-outside-toplevel

import functools
import os

from sky.utils import env_options
from sky.utils import ux_utils

kubernetes = None
urllib3 = None

_configured = False
_core_api = None
_auth_api = None
_networking_api = None
_custom_objects_api = None
_node_api = None

# Timeout to use for API calls
API_TIMEOUT = 5


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global kubernetes
        global urllib3
        if kubernetes is None:
            try:
                import kubernetes as _kubernetes
                import urllib3 as _urllib3
            except ImportError:
                # TODO(romilb): Update this message to point to installation
                #  docs when they are ready.
                raise ImportError('Fail to import dependencies for Kubernetes. '
                                  'Run `pip install kubernetes` to '
                                  'install them.') from None
            kubernetes = _kubernetes
            urllib3 = _urllib3
        return func(*args, **kwargs)

    return wrapper


@import_package
def get_kubernetes():
    return kubernetes


@import_package
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
                err_str = (
                    'Failed to load Kubernetes configuration. '
                    f'Please check if your kubeconfig file is valid.{suffix}')
            err_str += '\nTo disable Kubernetes for SkyPilot: run `sky check`.'
            with ux_utils.print_exception_no_traceback():
                raise ValueError(err_str) from None
    _configured = True


@import_package
def core_api():
    global _core_api
    if _core_api is None:
        _load_config()
        _core_api = kubernetes.client.CoreV1Api()

    return _core_api


@import_package
def auth_api():
    global _auth_api
    if _auth_api is None:
        _load_config()
        _auth_api = kubernetes.client.RbacAuthorizationV1Api()

    return _auth_api


@import_package
def networking_api():
    global _networking_api
    if _networking_api is None:
        _load_config()
        _networking_api = kubernetes.client.NetworkingV1Api()

    return _networking_api


@import_package
def custom_objects_api():
    global _custom_objects_api
    if _custom_objects_api is None:
        _load_config()
        _custom_objects_api = kubernetes.client.CustomObjectsApi()

    return _custom_objects_api


@import_package
def node_api():
    global _node_api
    if _node_api is None:
        _load_config()
        _node_api = kubernetes.client.NodeV1Api()

    return _node_api


@import_package
def api_exception():
    return kubernetes.client.rest.ApiException


@import_package
def config_exception():
    return kubernetes.config.config_exception.ConfigException


@import_package
def max_retry_error():
    return urllib3.exceptions.MaxRetryError


@import_package
def stream():
    return kubernetes.stream.stream

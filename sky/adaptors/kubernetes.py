"""Kubernetes adaptors"""

# pylint: disable=import-outside-toplevel

from functools import wraps

from sky.utils import ux_utils, env_options

kubernetes = None
urllib3 = None

_configured = False
_core_api = None
_auth_api = None
_networking_api = None
_custom_objects_api = None


def import_package(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        global kubernetes
        global urllib3
        if kubernetes is None:
            try:
                import kubernetes as _kubernetes
                import urllib3 as _urllib3
            except ImportError:
                raise ImportError('Fail to import dependencies for Kubernetes. '
                                  'See README for how to install it.') from None
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
        kubernetes.config.load_incluster_config()
    except kubernetes.config.config_exception.ConfigException:
        try:
            kubernetes.config.load_kube_config()
        except kubernetes.config.config_exception.ConfigException as e:
            with ux_utils.print_exception_no_traceback():
                suffix = ''
                if env_options.Options.SHOW_DEBUG_INFO.get():
                    suffix += f' Error: {str(e)}'
                raise ValueError('Failed to load Kubernetes configuration. '
                                 f'Please check your kubeconfig file is it valid. {suffix}') from None


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
def api_exception():
    return kubernetes.client.rest.ApiException


@import_package
def config_exception():
    return kubernetes.config.config_exception.ConfigException


@import_package
def max_retry_error():
    return urllib3.exceptions.MaxRetryError
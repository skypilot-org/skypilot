"""Kubernetes adaptors"""
import functools
import logging
import os
from typing import Any, Callable, Optional, Set

from sky.adaptors import common
from sky.sky_logging import set_logging_level
from sky.utils import env_options
from sky.utils import ux_utils

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Kubernetes. '
                         'Try running: pip install "skypilot[kubernetes]"')
kubernetes = common.LazyImport('kubernetes',
                               import_error_message=_IMPORT_ERROR_MESSAGE)
urllib3 = common.LazyImport('urllib3',
                            import_error_message=_IMPORT_ERROR_MESSAGE)

# Timeout to use for API calls
API_TIMEOUT = 5


def _decorate_methods(obj: Any, decorator: Callable, decoration_type: str):
    for attr_name in dir(obj):
        attr = getattr(obj, attr_name)
        # Skip methods starting with '__' since they are invoked through one
        # of the main methods, which are already decorated.
        if callable(attr) and not attr_name.startswith('__'):
            decorated_types: Set[str] = getattr(attr, '_sky_decorator_types',
                                                set())
            if decoration_type not in decorated_types:
                decorated_attr = decorator(attr)
                decorated_attr._sky_decorator_types = (  # pylint: disable=protected-access
                    decorated_types | {decoration_type})
                setattr(obj, attr_name, decorated_attr)
    return obj


def _api_logging_decorator(logger: str, level: int):
    """Decorator to set logging level for API calls.

    This is used to suppress the verbose logging from urllib3 when calls to the
    Kubernetes API timeout.
    """

    def decorated_api(api):

        def wrapped(*args, **kwargs):
            obj = api(*args, **kwargs)
            _decorate_methods(obj, set_logging_level(logger, level), 'api_log')
            return obj

        return wrapped

    return decorated_api


def _load_config(context: Optional[str] = None):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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
            kubernetes.config.load_kube_config(context=context)
        except kubernetes.config.config_exception.ConfigException as e:
            suffix = ''
            if env_options.Options.SHOW_DEBUG_INFO.get():
                suffix += f' Error: {str(e)}'
            # Check if exception was due to no current-context
            if 'Expected key current-context' in str(e):
                err_str = (
                    f'Failed to load Kubernetes configuration for {context!r}. '
                    'Kubeconfig does not contain any valid context(s).'
                    f'{suffix}\n'
                    '    If you were running a local Kubernetes '
                    'cluster, run `sky local up` to start the cluster.')
            else:
                err_str = (
                    f'Failed to load Kubernetes configuration for {context!r}. '
                    'Please check if your kubeconfig file exists at '
                    f'~/.kube/config and is valid.{suffix}')
            err_str += '\nTo disable Kubernetes for SkyPilot: run `sky check`.'
            with ux_utils.print_exception_no_traceback():
                raise ValueError(err_str) from None


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def core_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.CoreV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def auth_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.RbacAuthorizationV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def networking_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.NetworkingV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def custom_objects_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.CustomObjectsApi()


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def node_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.NodeV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def apps_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.AppsV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@functools.lru_cache()
def api_client(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.ApiClient()


def api_exception():
    return kubernetes.client.rest.ApiException


def config_exception():
    return kubernetes.config.config_exception.ConfigException


def max_retry_error():
    return urllib3.exceptions.MaxRetryError


def stream():
    return kubernetes.stream.stream

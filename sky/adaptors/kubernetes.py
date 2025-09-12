"""Kubernetes adaptors"""
import logging
import os
import platform
from typing import Any, Callable, Optional, Set

from sky import sky_logging
from sky.adaptors import common
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import ux_utils

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Kubernetes. '
                         'Try running: pip install "skypilot[kubernetes]"')
kubernetes = common.LazyImport('kubernetes',
                               import_error_message=_IMPORT_ERROR_MESSAGE)
models = common.LazyImport('kubernetes.client.models',
                           import_error_message=_IMPORT_ERROR_MESSAGE)
urllib3 = common.LazyImport('urllib3',
                            import_error_message=_IMPORT_ERROR_MESSAGE)
dateutil_parser = common.LazyImport('dateutil.parser',
                                    import_error_message=_IMPORT_ERROR_MESSAGE)

# Timeout to use for API calls
API_TIMEOUT = 5

# Check if KUBECONFIG is set, and use it if it is.
DEFAULT_KUBECONFIG_PATH = '~/.kube/config'
# From kubernetes package, keep a copy here to avoid actually importing
# kubernetes package when parsing the KUBECONFIG env var to do credential
# file mounts.
ENV_KUBECONFIG_PATH_SEPARATOR = ';' if platform.system() == 'Windows' else ':'

DEFAULT_IN_CLUSTER_REGION = 'in-cluster'
# The name for the environment variable that stores the in-cluster context name
# for Kubernetes clusters. This is used to associate a name with the current
# context when running with in-cluster auth. If not set, the context name is
# set to DEFAULT_IN_CLUSTER_REGION.
IN_CLUSTER_CONTEXT_NAME_ENV_VAR = 'SKYPILOT_IN_CLUSTER_CONTEXT_NAME'

logger = sky_logging.init_logger(__name__)


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


def _api_logging_decorator(logger_src: str, level: int):
    """Decorator to set logging level for API calls.

    This is used to suppress the verbose logging from urllib3 when calls to the
    Kubernetes API timeout.
    """

    def decorated_api(api):

        def wrapped(*args, **kwargs):
            obj = api(*args, **kwargs)
            _decorate_methods(obj,
                              sky_logging.set_logging_level(logger_src, level),
                              'api_log')
            return obj

        return wrapped

    return decorated_api


def _get_config_file() -> str:
    # Kubernetes load the kubeconfig from the KUBECONFIG env var on
    # package initialization. So we have to reload the KUBECOFNIG env var
    # everytime in case the KUBECONFIG env var is changed.
    return os.environ.get('KUBECONFIG', '~/.kube/config')


def _load_config(context: Optional[str] = None):
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _load_config_from_kubeconfig(context: Optional[str] = None):
        try:
            kubernetes.config.load_kube_config(config_file=_get_config_file(),
                                               context=context)
        except kubernetes.config.config_exception.ConfigException as e:
            suffix = common_utils.format_exception(e, use_bracket=True)
            context_name = '(current-context)' if context is None else context
            is_ssh_node_pool = False
            if context_name.startswith('ssh-'):
                context_name = common_utils.removeprefix(context_name, 'ssh-')
                is_ssh_node_pool = True
            # Check if exception was due to no current-context
            if 'Expected key current-context' in str(e):
                if is_ssh_node_pool:
                    context_name = common_utils.removeprefix(
                        context_name, 'ssh-')
                    err_str = ('Failed to load SSH Node Pool configuration for '
                               f'{context_name!r}.\n'
                               '    Run `sky ssh up --infra {context_name}` to '
                               'set up or repair the cluster.')
                else:
                    err_str = (
                        'Failed to load Kubernetes configuration for '
                        f'{context_name!r}. '
                        'Kubeconfig does not contain any valid context(s).'
                        f'\n{suffix}\n'
                        '    If you were running a local Kubernetes '
                        'cluster, run `sky local up` to start the cluster.')
            else:
                kubeconfig_path = os.environ.get('KUBECONFIG', '~/.kube/config')
                if is_ssh_node_pool:
                    err_str = (
                        f'Failed to load SSH Node Pool configuration for '
                        f'{context_name!r}. Run `sky ssh up --infra '
                        f'{context_name}` to set up or repair the cluster.')
                else:
                    err_str = (
                        'Failed to load Kubernetes configuration for '
                        f'{context_name!r}. Please check if your kubeconfig '
                        f'file exists at {kubeconfig_path} and is valid.'
                        f'\n{suffix}\n')
            if is_ssh_node_pool:
                err_str += (f'\nTo disable SSH Node Pool {context_name!r}: '
                            'run `sky check`.')
            else:
                err_str += (
                    '\nHint: Kubernetes attempted to query the current-context '
                    'set in kubeconfig. Check if the current-context is valid.')
            with ux_utils.print_exception_no_traceback():
                raise ValueError(err_str) from None

    if context == in_cluster_context_name() or context is None:
        try:
            # Load in-cluster config if running in a pod and context is None.
            # Kubernetes set environment variables for service discovery do not
            # show up in SkyPilot tasks. For now, we work around by using
            # DNS name instead of environment variables.
            # See issue: https://github.com/skypilot-org/skypilot/issues/2287
            # Only set if not already present (preserving existing values)
            if 'KUBERNETES_SERVICE_HOST' not in os.environ:
                os.environ['KUBERNETES_SERVICE_HOST'] = 'kubernetes.default.svc'
            if 'KUBERNETES_SERVICE_PORT' not in os.environ:
                os.environ['KUBERNETES_SERVICE_PORT'] = '443'
            kubernetes.config.load_incluster_config()
        except kubernetes.config.config_exception.ConfigException:
            _load_config_from_kubeconfig()
    else:
        _load_config_from_kubeconfig(context)


def list_kube_config_contexts():
    return kubernetes.config.list_kube_config_contexts(_get_config_file())


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def core_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.CoreV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def storage_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.StorageV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def auth_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.RbacAuthorizationV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def networking_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.NetworkingV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def custom_objects_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.CustomObjectsApi()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='global')
def node_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.NodeV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def apps_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.AppsV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def batch_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.BatchV1Api()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def api_client(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.ApiClient()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def custom_resources_api(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.client.CustomObjectsApi()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def watch(context: Optional[str] = None):
    _load_config(context)
    return kubernetes.watch.Watch()


def api_exception():
    return kubernetes.client.rest.ApiException


def config_exception():
    return kubernetes.config.config_exception.ConfigException


def max_retry_error():
    return urllib3.exceptions.MaxRetryError


def stream():
    return kubernetes.stream.stream


def in_cluster_context_name() -> Optional[str]:
    """Returns the name of the in-cluster context from the environment.

    If the environment variable is not set, returns the default in-cluster
    context name.
    """
    return (os.environ.get(IN_CLUSTER_CONTEXT_NAME_ENV_VAR) or
            DEFAULT_IN_CLUSTER_REGION)

"""Kubernetes adaptors

Thread safety notes:

The API functions (core_api, batch_api, etc.) return cached clients that are
created with context-specific ApiClient instances.

Set SKYPILOT_KUBECONFIG_REFRESH_INTERVAL_SECONDS (seconds) to refresh the
client proactively at a fixed interval so it is rebuilt from the updated
kubeconfig (e.g. for short-lived certs).
"""
import functools
import logging
import os
import platform
import threading
import time
import typing
from typing import Any, Callable, Optional, Set

from sky import sky_logging
from sky.adaptors import common
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import kubernetes
    import urllib3
    import urllib3.exceptions
else:
    _IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Kubernetes. '
                             'Try running: pip install "skypilot[kubernetes]"')
    kubernetes = common.LazyImport('kubernetes',
                                   import_error_message=_IMPORT_ERROR_MESSAGE)
    urllib3 = common.LazyImport('urllib3',
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
# If set (positive seconds), client is refreshed proactively after this
# interval.
KUBECONFIG_REFRESH_INTERVAL_ENV_VAR = (
    'SKYPILOT_KUBECONFIG_REFRESH_INTERVAL_SECONDS')

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
    # package initialization. So we have to reload the KUBECONFIG env var
    # everytime in case the KUBECONFIG env var is changed.
    return os.environ.get('KUBECONFIG', DEFAULT_KUBECONFIG_PATH)


def _get_api_client(context: Optional[str] = None) -> Any:
    """Get an ApiClient for the given context without modifying global config.

    This is fully thread-safe because it creates isolated Configuration
    objects for each client rather than modifying the global
    kubernetes.client.configuration.

    Args:
        context: The Kubernetes context to use. If None, tries in-cluster config
            first, then falls back to kubeconfig current-context.

    Returns:
        A kubernetes.client.ApiClient configured for the specified context.

    Raises:
        ValueError: If the configuration cannot be loaded.
    """
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _get_api_client_from_kubeconfig(context: Optional[str] = None) -> Any:
        """Load kubeconfig, return ApiClient without modifying global state."""
        try:
            # new_client_from_config returns an ApiClient configured for the
            # specified context WITHOUT modifying the global configuration.
            # This is the key to thread-safety.
            return kubernetes.config.new_client_from_config(
                config_file=_get_config_file(), context=context)
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
            # Use InClusterConfigLoader with an explicit Configuration object
            # to avoid modifying global state (thread-safe).
            #
            # Workaround: Kubernetes service discovery environment variables
            # may not show up in SkyPilot tasks. We set them to DNS names as
            # a fallback. See: github.com/skypilot-org/skypilot/issues/2287
            if 'KUBERNETES_SERVICE_HOST' not in os.environ:
                os.environ['KUBERNETES_SERVICE_HOST'] = 'kubernetes.default.svc'
            if 'KUBERNETES_SERVICE_PORT' not in os.environ:
                os.environ['KUBERNETES_SERVICE_PORT'] = '443'

            config = kubernetes.client.Configuration()
            kubernetes.config.load_incluster_config(config)
            return kubernetes.client.ApiClient(configuration=config)
        except kubernetes.config.config_exception.ConfigException:
            if context == in_cluster_context_name():
                # Explicitly requested in-cluster context but not in a cluster
                raise
            # Otherwise, if context is None, fall through to kubeconfig

    return _get_api_client_from_kubeconfig(context)


def list_kube_config_contexts():
    return kubernetes.config.list_kube_config_contexts(_get_config_file())


@functools.lru_cache(maxsize=None)
def _get_kubeconfig_refresh_interval_seconds() -> float:
    """Parse refresh interval from env; 0 means disabled.

    Result is cached because this is called on every k8s API method invocation
    and the env var is not expected to change at runtime.
    """
    raw = os.environ.get(KUBECONFIG_REFRESH_INTERVAL_ENV_VAR, '').strip()
    if not raw:
        return 0.0
    try:
        val = float(raw)
        return max(0.0, val)
    except ValueError:
        logger.warning(
            f'Invalid value for {KUBECONFIG_REFRESH_INTERVAL_ENV_VAR}: '
            f'"{raw}". Expected a numeric value. Disabling client '
            'refresh interval.')
        return 0.0


class RetryableClientWrapper:
    """Wrap a kubernetes client for interval-based refresh and resource cleanup.

    Each wrapper tracks its own last-refresh time and refreshes only its
    underlying client when the configured interval has elapsed, without
    invalidating other wrappers or global caches. Closes the underlying
    ApiClient on GC to release external resources (e.g. semaphores).
    """

    def __init__(self, client: Any, getter: Callable, getter_args: tuple,
                 getter_kwargs: dict):
        self._client = client
        self._getter = getter
        self._getter_args = getter_args
        self._getter_kwargs = getter_kwargs
        self._last_refresh_time = time.time()
        self._refresh_lock = threading.Lock()

    def _should_refresh(self) -> bool:
        """True if this wrapper's refresh interval has elapsed."""
        interval = _get_kubeconfig_refresh_interval_seconds()
        if interval <= 0:
            return False
        return (time.time() - self._last_refresh_time) >= interval

    def _close_client(self, client: Any) -> None:
        """Close the underlying ApiClient to release external resources."""
        try:
            real_client = None
            if isinstance(client, kubernetes.client.ApiClient):
                real_client = client
            elif isinstance(client, kubernetes.watch.Watch):
                real_client = getattr(client, '_api_client', None)
            else:
                # Typed clients (CoreV1Api etc.) are codegen'd and all have
                # an 'api_client' attribute pointing to the real ApiClient.
                real_client = getattr(client, 'api_client', None)
            if real_client is not None:
                real_client.close()
            else:
                # logger may already be cleaned up during __del__ at shutdown
                if logger is not None:
                    logger.debug(f'No client found for {client}')
        except Exception as e:  # pylint: disable=broad-except
            if logger is not None:
                logger.debug(f'Error closing Kubernetes client: {e}')

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def with_refresh(*args, **kwargs):
            if self._should_refresh():
                with self._refresh_lock:
                    if self._should_refresh():
                        logger.debug(
                            'Refreshing Kubernetes client from kubeconfig '
                            'due to interval expiry.')
                        old_client = self._client
                        self._client = self._getter(*self._getter_args,
                                                    **self._getter_kwargs)
                        self._last_refresh_time = time.time()
                        self._close_client(old_client)
            method = getattr(self._client, name)
            return method(*args, **kwargs)

        # Cache on the instance so repeated accesses to the same method name
        # return the same closure without going through __getattr__ again.
        # The closure always reads self._client at call time, so it stays
        # correct after a client refresh.
        self.__dict__[name] = with_refresh
        return with_refresh

    def __del__(self):
        self._close_client(self._client)


def _retryable_kubernetes_client(getter: Callable) -> Callable:
    """Wrap a kubernetes client getter in a RetryableClientWrapper.

    On each call the getter is invoked to obtain the raw client, which is then
    wrapped so it can be transparently refreshed when the configured kubeconfig
    refresh interval has elapsed.
    """

    @functools.wraps(getter)
    def wrapper(*args: Any, **kwargs: Any) -> RetryableClientWrapper:
        client = getter(*args, **kwargs)
        return RetryableClientWrapper(client, getter, args, kwargs)

    return wrapper


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def core_api(context: Optional[str] = None):
    return kubernetes.client.CoreV1Api(api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def storage_api(context: Optional[str] = None):
    return kubernetes.client.StorageV1Api(api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def auth_api(context: Optional[str] = None):
    return kubernetes.client.RbacAuthorizationV1Api(
        api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def networking_api(context: Optional[str] = None):
    return kubernetes.client.NetworkingV1Api(
        api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def custom_objects_api(context: Optional[str] = None):
    return kubernetes.client.CustomObjectsApi(
        api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def node_api(context: Optional[str] = None):
    return kubernetes.client.NodeV1Api(api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def apps_api(context: Optional[str] = None):
    return kubernetes.client.AppsV1Api(api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def batch_api(context: Optional[str] = None):
    return kubernetes.client.BatchV1Api(api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def api_client(context: Optional[str] = None):
    return _get_api_client(context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def custom_resources_api(context: Optional[str] = None):
    return kubernetes.client.CustomObjectsApi(
        api_client=_get_api_client(context))


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
@_retryable_kubernetes_client
def watch(context: Optional[str] = None):
    w = kubernetes.watch.Watch()
    w._api_client = _get_api_client(context)  # pylint: disable=protected-access
    return w


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

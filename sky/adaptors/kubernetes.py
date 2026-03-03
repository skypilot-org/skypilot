"""Kubernetes adaptors

Thread safety notes:

The API functions (core_api, batch_api, etc.) return cached clients that are
created with context-specific ApiClient instances.

Teleport tbot certificate rotation: When using Teleport's tbot for short-lived
kubeconfig certificates, SSL errors can occur after rotation. The adaptor
retries API calls on SSL-related errors (with a short delay to allow tbot to
refresh the cert) after clearing client caches so that a new client is built
from the updated kubeconfig (e.g. from KUBECONFIG=/var/lib/tbot/kubeconfig/
kubeconfig.yaml).
"""
import functools
import logging
import os
import platform
import ssl
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

# Default path for Teleport tbot kubeconfig (used when KUBECONFIG is not set
# and API server runs with Teleport). Set KUBECONFIG to this path to enable
# automatic cert rotation.
TELEPORT_TBOT_KUBECONFIG_PATH = '/var/lib/tbot/kubeconfig/kubeconfig.yaml'

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
    # package initialization. So we have to reload the KUBECONFIG env var
    # every time in case the KUBECONFIG env var is changed.
    return os.environ.get('KUBECONFIG', DEFAULT_KUBECONFIG_PATH)


def _is_ssl_related_error(exc: BaseException) -> bool:
    """True if the exception is an SSL/certificate error (e.g. after cert rotation)."""
    if exc is None:
        return False
    if isinstance(exc, ssl.SSLError):
        return True
    if hasattr(urllib3.exceptions, 'SSLError') and isinstance(
            exc, urllib3.exceptions.SSLError):
        return True
    if hasattr(urllib3.exceptions, 'MaxRetryError') and isinstance(
            exc, urllib3.exceptions.MaxRetryError):
        reason = getattr(exc, 'reason', None)
        if reason is not None and _is_ssl_related_error(reason):
            return True
    cause = getattr(exc, '__cause__', None)
    if cause is not None and _is_ssl_related_error(cause):
        return True
    # Fallback: detect by message (e.g. heavily wrapped or custom exception)
    msg = str(exc).lower()
    if 'certificate expired' in msg or 'sslv3_alert_certificate_expired' in msg:
        return True
    return False


def is_ssl_related_error(exc: BaseException) -> bool:
    """Public helper to detect SSL/cert errors (e.g. for outer retry logic)."""
    return _is_ssl_related_error(exc)


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


class ClientWrapper:
    """Wrapper around the kubernetes API clients.

    This is needed because we cache kubernetes.client.ApiClient and other typed
    clients (e.g. kubernetes.client.CoreV1Api) and lru_cache.cache_clear() does
    not call close() on the client to cleanup external resources like
    semaphores. This decorator wraps the client with __del__ to ensure the
    external state of kubernetes clients are properly cleaned up on GC.
    """

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        """Delegate to the underlying client"""
        return getattr(self._client, name)

    def __del__(self):
        """Clean up the underlying client"""
        try:
            real_client = None
            if isinstance(self._client, kubernetes.client.ApiClient):
                real_client = self._client
            elif isinstance(self._client, kubernetes.watch.Watch):
                real_client = getattr(self._client, '_api_client', None)
            else:
                # Otherwise, the client is a typed client, the typed client
                # is generated by codegen and all of them should have an
                # 'api_client' attribute referring to the real client.
                real_client = getattr(self._client, 'api_client', None)
            if real_client is not None:
                real_client.close()
            else:
                # logger may already be cleaned up during __del__ at shutdown
                if logger is not None:
                    logger.debug(f'No client found for {self._client}')
        except Exception as e:  # pylint: disable=broad-except
            if logger is not None:
                logger.debug(f'Error closing Kubernetes client: {e}')


# Max retries for SSL/cert errors (e.g. Teleport tbot rotation). Delay between
# retries gives tbot time to write updated kubeconfig.
_SSL_RETRY_ATTEMPTS = 3
_SSL_RETRY_DELAY_SECONDS = 2


class RetryableClientWrapper:
    """Wraps a Kubernetes API client to retry on SSL/cert errors.

    When a method call raises an SSL-related error (e.g. after Teleport tbot
    certificate rotation), clears client caches and retries with a short delay
    so a new client is built from the updated kubeconfig.
    """

    def __init__(self, client: Any, getter: Callable, getter_args: tuple,
                 getter_kwargs: dict):
        self._client = client
        self._getter = getter
        self._getter_args = getter_args
        self._getter_kwargs = getter_kwargs

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def with_retry(*args, **kwargs):
            last_exc = None
            for attempt in range(_SSL_RETRY_ATTEMPTS):
                try:
                    if attempt == 0:
                        client = self._client
                    else:
                        time.sleep(_SSL_RETRY_DELAY_SECONDS)
                        _clear_kubernetes_client_caches()
                        client = self._getter(*self._getter_args,
                                              **self._getter_kwargs)
                    method = getattr(client, name)
                    return method(*args, **kwargs)
                except Exception as e:  # pylint: disable=broad-except
                    last_exc = e
                    if not _is_ssl_related_error(e):
                        raise
                    logger.debug(
                        'Kubernetes SSL/cert error (attempt %s/%s), clearing '
                        'caches and retrying: %s',
                        attempt + 1,
                        _SSL_RETRY_ATTEMPTS,
                        e,
                    )
            if last_exc is not None:
                raise last_exc
            raise RuntimeError('Unexpected: SSL retry loop exited without exception')

        return with_retry


def _make_retryable(client: Any, getter: Callable, *args: Any,
                    **kwargs: Any) -> RetryableClientWrapper:
    """Wrap a Kubernetes client so one retry is done on SSL/cert errors."""
    return RetryableClientWrapper(client, getter, args, kwargs)


def wrap_kubernetes_client(func):
    """Wraps kubernetes API clients for proper cleanup."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        obj = func(*args, **kwargs)
        return ClientWrapper(obj)

    return wrapper


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def core_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.CoreV1Api(api_client=_get_api_client(context)))
    return _make_retryable(client, core_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def storage_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.StorageV1Api(api_client=_get_api_client(context)))
    return _make_retryable(client, storage_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def auth_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.RbacAuthorizationV1Api(
            api_client=_get_api_client(context)))
    return _make_retryable(client, auth_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def networking_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.NetworkingV1Api(api_client=_get_api_client(context)))
    return _make_retryable(client, networking_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def custom_objects_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.CustomObjectsApi(api_client=_get_api_client(context)))
    return _make_retryable(client, custom_objects_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def node_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.NodeV1Api(api_client=_get_api_client(context)))
    return _make_retryable(client, node_api, context)


def _clear_kubernetes_client_caches() -> None:
    """Clear Kubernetes API client caches so the next call rebuilds from kubeconfig.

    Used after SSL/cert errors (e.g. Teleport tbot rotation) so clients are
    recreated with updated certificates. All API getters (including node_api) use
    request-scoped cache, so clearing the request-level cache is sufficient.
    """
    annotations.clear_request_level_cache()


def clear_kubernetes_client_caches() -> None:
    """Clear Kubernetes API client caches (e.g. before outer retries on SSL errors)."""
    _clear_kubernetes_client_caches()


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def apps_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.AppsV1Api(api_client=_get_api_client(context)))
    return _make_retryable(client, apps_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def batch_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.BatchV1Api(api_client=_get_api_client(context)))
    return _make_retryable(client, batch_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def api_client(context: Optional[str] = None):
    client = ClientWrapper(_get_api_client(context))
    return _make_retryable(client, api_client, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def custom_resources_api(context: Optional[str] = None):
    client = ClientWrapper(
        kubernetes.client.CustomObjectsApi(api_client=_get_api_client(context)))
    return _make_retryable(client, custom_resources_api, context)


@_api_logging_decorator('urllib3', logging.ERROR)
@annotations.lru_cache(scope='request')
def watch(context: Optional[str] = None):
    w = kubernetes.watch.Watch()
    w._api_client = _get_api_client(context)  # pylint: disable=protected-access
    return _make_retryable(ClientWrapper(w), watch, context)


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

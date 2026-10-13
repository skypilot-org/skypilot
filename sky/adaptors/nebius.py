"""Nebius cloud adaptor."""
import asyncio
import logging
import os
import threading
from typing import (Any, Awaitable, Dict, List, NamedTuple, Optional, Tuple,
                    Union)
import weakref

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common
from sky.utils import annotations
from sky.utils import ux_utils

# Default read timeout for nebius SDK
READ_TIMEOUT = 10

logger = sky_logging.init_logger(__name__)

_loop_lock = threading.Lock()
_loop = None


def _loop_exception_handler(loop: asyncio.AbstractEventLoop,
                            context: Dict[str, Any]) -> None:
    """Exception handler for the dedicated nebius SDK event loop.

    grpc.aio can raise exceptions in loop callbacks (e.g.
    PollerCompletionQueue._handle_events) when a nebius API call fails.
    The default asyncio handler logs these at ERROR level to stderr,
    polluting user-facing CLI output. API errors are still raised to
    callers through sync_call(), so log the callback noise at debug level
    for diagnostics instead. This handler is scoped to the dedicated
    nebius event loop and does not affect other event loops.
    """
    del loop  # Unused.
    message = context.get('message', 'Exception in nebius SDK event loop')
    exception = context.get('exception')
    exc_info: Any = False
    if exception is not None:
        exc_info = (type(exception), exception, exception.__traceback__)
    logger.debug(f'nebius SDK event loop: {message}', exc_info=exc_info)


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Get event loop for nebius sdk."""
    global _loop

    if _loop is not None:
        return _loop

    with _loop_lock:
        if _loop is None:
            # Create a new event loop in a dedicated thread
            loop = asyncio.new_event_loop()
            loop.set_exception_handler(_loop_exception_handler)
            threading.Thread(target=loop.run_forever, daemon=True).start()
            _loop = loop

        return _loop


def sync_call(awaitable: Awaitable[Any]) -> Any:
    """Synchronously run an awaitable in coroutine.

    This wrapper is used to workaround:
    https://github.com/nebius/pysdk/issues/76

    Uses a dedicated background event loop to avoid conflicts
    with existing asyncio contexts and prevent BlockingIOError.
    """
    loop = _get_event_loop()
    future = asyncio.run_coroutine_threadsafe(_coro(awaitable), loop)
    return future.result()


async def _coro(awaitable: Awaitable[Any]) -> Any:
    """Wrapper coroutine for awaitable."""
    return await awaitable


def tenant_id_path() -> str:
    return '~/.nebius/NEBIUS_TENANT_ID.txt'


def iam_token_path() -> str:
    return '~/.nebius/NEBIUS_IAM_TOKEN.txt'


def domain_path() -> str:
    return '~/.nebius/NEBIUS_DOMAIN.txt'


def credentials_path() -> str:
    workspace_path = skypilot_config.get_workspace_cloud('nebius').get(
        'credentials_file_path', None)
    if workspace_path is not None:
        return workspace_path
    return _get_default_credentials_path()


def _get_workspace_credentials_path() -> Optional[str]:
    """Get credentials path if explicitly set in workspace config."""
    workspace_cred_path = skypilot_config.get_workspace_cloud('nebius').get(
        'credentials_file_path', None)
    return workspace_cred_path


def _get_default_credentials_path() -> str:
    """Get the default credentials path."""
    return '~/.nebius/credentials.json'


def api_domain() -> Optional[str]:
    domain_in_ws_config = skypilot_config.get_workspace_cloud('nebius').get(
        'domain', None)
    if domain_in_ws_config is not None:
        return domain_in_ws_config
    domain_in_config = skypilot_config.get_effective_region_config(
        cloud='nebius', region=None, keys=('domain',), default_value=None)
    if domain_in_config is not None:
        return domain_in_config
    try:
        with open(os.path.expanduser(domain_path()), encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


DEFAULT_REGION = 'eu-north1'

NEBIUS_PROFILE_NAME = 'nebius'

MAX_RETRIES_TO_DISK_CREATE = 120
MAX_RETRIES_TO_INSTANCE_STOP = 120
MAX_RETRIES_TO_INSTANCE_START = 120
MAX_RETRIES_TO_INSTANCE_READY = 240

MAX_RETRIES_TO_DISK_DELETE = 120
MAX_RETRIES_TO_INSTANCE_WAIT = 120  # Maximum number of retries

POLL_INTERVAL = 5

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Nebius AI Cloud.'
                         'Try pip install "skypilot[nebius]"')


class _NebiusDeprecationFilter(logging.Filter):
    """Drops nebius SDK records logged to the 'deprecation' logger.

    The nebius SDK emits deprecated-field warnings (e.g.
    'Field .nebius.compute.v1.PreemptibleSpec.priority is deprecated')
    to a logger named 'deprecation'. They are not actionable by users and
    pollute user-facing CLI output. Only records originating from the
    nebius package are dropped, so other libraries using the same logger
    name are unaffected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return f'{os.sep}nebius{os.sep}' not in record.pathname


class _NebiusGrpcPollerFilter(logging.Filter):
    """Downgrades grpc.aio poller errors logged to the 'asyncio' logger.

    The nebius SDK's grpc.aio channels raise exceptions in event-loop
    callbacks (PollerCompletionQueue._handle_events, e.g.
    'BlockingIOError: [Errno 11] Resource temporarily unavailable') when
    the SDK's pollers race with fd availability. asyncio's default
    exception handler logs them at ERROR to the 'asyncio' logger, which
    propagates to the root logger and pollutes user-facing CLI output.
    The exceptions are internal to grpc's poller and are not actionable;
    real API errors are still raised to callers through sync_call().

    The loop-scoped handler installed by _get_event_loop() only covers
    the dedicated loop used by sync_call(), but the SDK also runs
    pollers on its own internal loops, whose records we can only
    intercept here. The records are re-logged at debug level instead of
    being dropped, so SKYPILOT_DEBUG=1 still shows them for diagnostics.

    Only records mentioning the grpc poller are downgraded, so unrelated
    asyncio errors (from any library) keep their original level.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if 'PollerCompletionQueue' not in record.getMessage():
            return True
        logger.debug('nebius SDK grpc.aio poller: %s',
                     record.getMessage(),
                     exc_info=record.exc_info)
        return False


def _set_nebius_loggers() -> None:
    # https://github.com/grpc/grpc/issues/37642 to avoid spam in console
    os.environ['GRPC_VERBOSITY'] = 'NONE'
    logging.getLogger('deprecation').addFilter(_NebiusDeprecationFilter())
    logging.getLogger('asyncio').addFilter(_NebiusGrpcPollerFilter())


nebius = common.LazyImport('nebius',
                           import_error_message=_IMPORT_ERROR_MESSAGE,
                           set_loggers=_set_nebius_loggers)
boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore, nebius)
_session_creation_lock = threading.RLock()
_INDENT_PREFIX = '    '
NAME = 'Nebius'
SKY_CHECK_NAME = 'Nebius (for Nebius Object Storae)'


def request_error():
    # pylint: disable=import-outside-toplevel
    from nebius.aio import service_error
    return service_error.RequestError


def status_code():
    # pylint: disable=import-outside-toplevel
    from nebius.aio.service_error import StatusCode
    return StatusCode


def compute():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.compute import v1 as compute_v1
    return compute_v1


def iam():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.iam import v1 as iam_v1
    return iam_v1


def billing():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.billing import v1 as billing_v1
    return billing_v1


def nebius_common():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.common import v1 as common_v1
    return common_v1


def vpc():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.vpc import v1 as vpc_v1
    return vpc_v1


def get_iam_token():
    try:
        with open(os.path.expanduser(iam_token_path()),
                  encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


def is_token_or_cred_file_exist():
    return (os.path.exists(os.path.expanduser(iam_token_path())) or
            os.path.exists(os.path.expanduser(credentials_path())))


def get_tenant_id():
    tenant_id_in_ws_config = skypilot_config.get_workspace_cloud('nebius').get(
        'tenant_id', None)
    if tenant_id_in_ws_config is not None:
        return tenant_id_in_ws_config
    tenant_id_in_config = skypilot_config.get_effective_region_config(
        cloud='nebius', region=None, keys=('tenant_id',), default_value=None)
    if tenant_id_in_config is not None:
        return tenant_id_in_config
    try:
        with open(os.path.expanduser(tenant_id_path()),
                  encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


def sdk():
    """Create the Nebius SDK with the correct credentials.

    The order of priority is:
    1. Credentials file specified in workspace config, if set
    2. IAM token file, if set
    3. Default credentials path
    """
    # 1. Check if credentials path is set in workspace config (highest priority)
    workspace_cred_path = _get_workspace_credentials_path()
    if workspace_cred_path is not None:
        # Check if token is also available and warn
        token = get_iam_token()
        if token is not None:
            logger.warning(
                f'Both workspace credentials file ({workspace_cred_path}) and '
                f'IAM token file ({iam_token_path()}) are available. Using '
                'workspace credentials file.')
        return _sdk(None, workspace_cred_path)

    # 2. Check for IAM token file (second priority)
    token = get_iam_token()
    if token is not None:
        return _sdk(token, None)

    # 3. Fall back to default credentials path (lowest priority)
    default_cred_path = _get_default_credentials_path()
    return _sdk(None, default_cred_path)


def _user_agent_prefix() -> str:
    # Import locally to avoid a circular import while sky is initialized.
    # pylint: disable=import-outside-toplevel
    from sky import __version__
    return f'skypilot/{__version__}'


# SDKs own a loop thread, channel pool and token-renewal task. Dropping a
# request-scoped cache entry does not release those resources. Reuse clients
# across requests, serializing construction to avoid concurrent cache misses.
_SDK_CLOSE_TIMEOUT = 30
_sdk_lock = threading.Lock()


class _SDKHandle:
    """Keep an SDK alive while service clients, requests or operations use it.

    The SDK's renewal task retains the raw SDK, so finalizing the SDK itself
    cannot reclaim it. This separate handle is retained by the cache and SDK
    consumers, but not by the SDK's internal background tasks.
    """

    def __init__(self, sdk_client: Any) -> None:
        # Loop creation can trigger GC while holding _loop_lock. Initialize it
        # before registering any finalizer that could try to acquire that lock.
        _get_event_loop()
        self._sdk_client = sdk_client
        # At interpreter exit the OS reclaims process-owned resources. Do not
        # attempt to start/join event-loop threads during interpreter shutdown.
        finalizer = weakref.finalize(self, _close_sdk, sdk_client)
        # Typeshed declares this writable property as a field outside __slots__.
        finalizer.atexit = False  # type: ignore[misc]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sdk_client, name)


class _SDKCacheEntry(NamedTuple):
    credential_version: Union[str, Tuple[int, int, int, int]]
    client: _SDKHandle


_SDKSource = Tuple[bool, str, Optional[str]]
_sdk_cache: Dict[_SDKSource, _SDKCacheEntry] = {}
_sdk_sources: Dict[Tuple[str, Optional[str]], _SDKSource] = {}


async def _close_sdk_async(sdk_client: Any) -> None:
    try:
        await asyncio.wait_for(sdk_client.close(), timeout=_SDK_CLOSE_TIMEOUT)
    except Exception:  # pylint: disable=broad-except
        logger.warning('Failed to close a cached Nebius SDK.', exc_info=True)


def _close_sdk(sdk_client: Any) -> None:
    # The last reference can disappear on an SDK thread or an async caller's
    # loop. Never block that thread while waiting for the SDK to shut down.
    asyncio.run_coroutine_threadsafe(_close_sdk_async(sdk_client),
                                     _get_event_loop())


def _sdk(token: Optional[str], cred_path: Optional[str]):
    # Exactly one of token or cred_path must be provided.
    assert (token is None) != (cred_path is None), (token, cred_path)
    domain = api_domain()
    source_path = os.path.abspath(
        os.path.expanduser(
            cred_path if cred_path is not None else iam_token_path()))
    source = (token is not None, source_path, domain)
    owner = (skypilot_config.get_active_workspace(), domain)
    with _sdk_lock:
        credential_version: Union[str, Tuple[int, int, int, int]]
        if token is not None:
            credential_version = token
        else:
            # The SDK reads service-account credentials only at construction.
            # Preserve credential refresh when a file is edited or replaced.
            stat = os.stat(source_path)
            credential_version = (stat.st_ino, stat.st_size, stat.st_mtime_ns,
                                  stat.st_ctime_ns)
        entry = _sdk_cache.get(source)
        if (entry is None or entry.credential_version != credential_version):
            if token is not None:
                sdk_client = nebius.sdk.SDK(
                    credentials=token,
                    domain=domain,
                    user_agent_prefix=_user_agent_prefix())
            else:
                sdk_client = nebius.sdk.SDK(
                    credentials_file_name=source_path,
                    domain=domain,
                    user_agent_prefix=_user_agent_prefix())
            entry = _SDKCacheEntry(credential_version, _SDKHandle(sdk_client))
            _sdk_cache[source] = entry

        # A source switch (e.g. adding/removing the IAM token file) must also
        # retire the previous client, unless another workspace still selects it.
        previous_source = _sdk_sources.get(owner)
        _sdk_sources[owner] = source
        if (previous_source is not None and previous_source != source and
                previous_source not in _sdk_sources.values()):
            _sdk_cache.pop(previous_source, None)
        return entry.client


def clear_sdk_cache() -> None:
    """Retire cached SDKs without interrupting their existing consumers.

    SDKs close asynchronously after the last service client, request or
    operation releases its handle. The catalog fetcher calls this between
    asyncio.run() cycles; concurrent callers can safely finish on retired SDKs.
    """
    with _sdk_lock:
        _sdk_cache.clear()
        _sdk_sources.clear()


def get_nebius_credentials(boto3_session):
    """Gets the Nebius credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the R2 credentials.
    """
    nebius_credentials = boto3_session.get_credentials()
    if nebius_credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Nebius credentials not found. Run '
                             '`sky check` to verify credentials are '
                             'correctly set up.')
    return nebius_credentials.get_frozen_credentials()


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache # pylint: disable=line-too-long
@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        session_ = boto3.session.Session(profile_name=NEBIUS_PROFILE_NAME)
    return session_


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create a Nebius resource.

    Args:
        resource_name: Nebius resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()

    return session_.resource(resource_name, **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str):
    """Create Nebius client of a certain service.

    Args:
        service_name: Nebius service name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()

    return session_.client(service_name)


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions
    return exceptions


def get_credential_file_paths() -> List[str]:
    """Get the list of credential file paths based on current configuration."""
    paths = {
        # Always include tenant ID and IAM token paths
        tenant_id_path(),
        iam_token_path(),
    }

    # Add workspace-specific credentials path if set
    workspace_cred_path = _get_workspace_credentials_path()
    if workspace_cred_path is not None:
        paths.add(workspace_cred_path)
    # Always add default path in case it's needed for fallback
    paths.add(_get_default_credentials_path())

    return list(paths)

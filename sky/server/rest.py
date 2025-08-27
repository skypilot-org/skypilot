"""REST API client of SkyPilot API server"""

import asyncio
import contextlib
import contextvars
import functools
import time
import typing
from typing import Any, Callable, cast, Optional, TypeVar

import colorama

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.server import constants
from sky.server import versions
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    import aiohttp
    import requests

else:
    aiohttp = adaptors_common.LazyImport('aiohttp')
    requests = adaptors_common.LazyImport('requests')

F = TypeVar('F', bound=Callable[..., Any])

_RETRY_CONTEXT = contextvars.ContextVar('retry_context', default=None)

_session = requests.Session()
# Tune connection pool size, otherwise the default max is just 10.
adapter = requests.adapters.HTTPAdapter(
    pool_connections=50,
    pool_maxsize=200,
    # We handle retries by ourselves in SDK.
    max_retries=0,
)
_session.mount('http://', adapter)
_session.mount('https://', adapter)

_session.headers[constants.API_VERSION_HEADER] = str(constants.API_VERSION)
_session.headers[constants.VERSION_HEADER] = (
    versions.get_local_readable_version())

# Enumerate error types that might be transient and can be addressed by
# retrying.
_transient_errors = [
    requests.exceptions.RequestException,
    ConnectionError,
]


class RetryContext:

    def __init__(self):
        self.line_processed = 0


@contextlib.contextmanager
def _retry_in_context():
    context = RetryContext()
    token = _RETRY_CONTEXT.set(context)
    try:
        yield context
    finally:
        _RETRY_CONTEXT.reset(token)


def get_retry_context() -> Optional[RetryContext]:
    return _RETRY_CONTEXT.get()


def retry_transient_errors(max_retries: int = 3,
                           initial_backoff=1,
                           max_backoff_factor=5):
    """Decorator that retries a function when a transient error is caught.

    This decorator is mainly used to decorate idempotent SDK functions to make
    it more robust to transient errors.

    Args:
        max_retries: Maximum number of retries
        initial_backoff: Initial backoff time in seconds
        max_backoff_factor: Maximum backoff factor for exponential backoff
    """

    def is_transient_error(e: Exception) -> bool:
        if isinstance(e, requests.exceptions.HTTPError):
            # Only server error is considered as transient.
            return e.response.status_code >= 500
        for error in _transient_errors:
            if isinstance(e, error):
                return True
        return False

    def decorator(func: F) -> F:

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            backoff = common_utils.Backoff(initial_backoff, max_backoff_factor)
            consecutive_failed_count = 0

            with _retry_in_context() as context:
                previous_line_processed = context.line_processed  # should be 0

                def _handle_exception():
                    # If the function made progress on a retry,
                    # clears the backoff and resets the failed retry count.
                    # Otherwise, increments the failed retry count.
                    nonlocal backoff
                    nonlocal consecutive_failed_count
                    nonlocal previous_line_processed
                    if context.line_processed > previous_line_processed:
                        backoff = common_utils.Backoff(initial_backoff,
                                                       max_backoff_factor)
                        previous_line_processed = context.line_processed
                        consecutive_failed_count = 0
                    else:
                        consecutive_failed_count += 1

                while consecutive_failed_count < max_retries:
                    try:
                        return func(*args, **kwargs)
                    # Occurs when the server proactively interrupts the request
                    # during rolling update, we can retry immediately on the
                    # new replica.
                    except exceptions.RequestInterruptedError:
                        _handle_exception()
                        logger.debug('Request interrupted. Retry immediately.')
                        continue
                    except Exception as e:  # pylint: disable=broad-except
                        _handle_exception()
                        if consecutive_failed_count >= max_retries:
                            # Retries exhausted.
                            raise
                        if not is_transient_error(e):
                            # Permanent error, no need to retry.
                            raise
                        logger.debug(
                            f'Retry {func.__name__} due to {e}, '
                            f'attempt {consecutive_failed_count}/{max_retries}')
                        # Only sleep if this is not the first retry.
                        # The idea is that if the function made progress on a
                        # retry, we should try again immediately to reduce the
                        # waiting time.
                        if consecutive_failed_count > 0:
                            time.sleep(backoff.current_backoff())

        return cast(F, wrapper)

    return decorator


def _retry_on_server_unavailable(max_wait_seconds: int = 600,
                                 initial_backoff: float = 5.0,
                                 max_backoff_factor: int = 5):
    """Decorator that retries a function when ServerTemporarilyUnavailableError
    is caught.

    This decorator is mainly used to decorate a Restful API call to make
    the API call wait for server recovery when server is temporarily
    unavailable.

    Args:
        max_wait_seconds: Maximum number of seconds to wait for the server to
            be healthy
        initial_backoff: Initial backoff time in seconds
        max_backoff_factor: Maximum backoff factor for exponential backoff

    Notes(dev):
    """

    def decorator(func: F) -> F:

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            msg = (
                f'{colorama.Fore.YELLOW}API server is temporarily unavailable: '
                'upgrade in progress. Waiting to resume...'
                f'{colorama.Style.RESET_ALL}')
            backoff = common_utils.Backoff(
                initial_backoff=initial_backoff,
                max_backoff_factor=max_backoff_factor)
            start_time = time.time()
            attempt = 0

            with _retry_in_context():
                while True:
                    attempt += 1
                    try:
                        return func(*args, **kwargs)
                    except exceptions.ServerTemporarilyUnavailableError as e:
                        # This will cause the status spinner being stopped and
                        # restarted in every retry loop. But it is necessary to
                        # stop the status spinner before retrying func() to
                        # avoid the status spinner get stuck if the func() runs
                        # for a long time without update status, e.g. sky logs.
                        with rich_utils.client_status(msg):
                            if time.time() - start_time > max_wait_seconds:
                                # pylint: disable=line-too-long
                                raise exceptions.ServerTemporarilyUnavailableError(
                                    'Timeout waiting for the API server to be '
                                    f'available after {max_wait_seconds}s.') \
                                    from e

                            sleep_time = backoff.current_backoff()
                            time.sleep(sleep_time)
                            logger.debug('The API server is unavailable. '
                                         f'Retrying {func.__name__} '
                                         f'(attempt {attempt}, '
                                         f'backoff {sleep_time}s).')

        return cast(F, wrapper)

    return decorator


def handle_server_unavailable(response: 'requests.Response') -> None:
    if response.status_code == 503:
        # TODO(aylei): Hacky, depends on how nginx controller handles backends
        # with no ready endpoints. Should use self-defined status code or header
        # to distinguish retryable server error from general 503 errors.
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ServerTemporarilyUnavailableError(
                'SkyPilot API server is temporarily unavailable. '
                'Please try again later.')


@_retry_on_server_unavailable()
def request(method, url, **kwargs) -> 'requests.Response':
    """Send a request to the API server, retry on server temporarily
    unavailable."""
    return request_without_retry(method, url, **kwargs)


def request_without_retry(method, url, **kwargs) -> 'requests.Response':
    """Send a request to the API server without retry."""
    response = _session.request(method, url, **kwargs)
    handle_server_unavailable(response)
    remote_api_version = response.headers.get(constants.API_VERSION_HEADER)
    remote_version = response.headers.get(constants.VERSION_HEADER)
    if remote_api_version is not None:
        versions.set_remote_api_version(int(remote_api_version))
    if remote_version is not None:
        versions.set_remote_version(remote_version)
    return response


# Async versions of the above functions


async def request_async(session: 'aiohttp.ClientSession', method: str, url: str,
                        **kwargs) -> 'aiohttp.ClientResponse':
    """Send an async request to the API server, retry on server temporarily
    unavailable."""
    max_retries = 3
    initial_backoff = 1.0
    max_backoff_factor = 5

    backoff = common_utils.Backoff(initial_backoff, max_backoff_factor)
    last_exception = Exception('Uknown Exception')  # this will be replaced by e

    for retry_count in range(max_retries):
        try:
            return await request_without_retry_async(session, method, url,
                                                     **kwargs)
        except exceptions.RequestInterruptedError:
            logger.debug('Request interrupted. Retry immediately.')
            continue
        except Exception as e:  # pylint: disable=broad-except
            last_exception = e
            if retry_count >= max_retries - 1:
                # Retries exhausted
                raise

            # Check if this is a transient error (similar to sync version logic)
            is_transient = _is_transient_error_async(e)
            if not is_transient:
                # Permanent error, no need to retry
                raise

            logger.debug(f'Retry async request due to {e}, '
                         f'attempt {retry_count + 1}/{max_retries}')
            await asyncio.sleep(backoff.current_backoff())

    # This should never be reached, but just in case
    raise last_exception


async def request_without_retry_async(session: 'aiohttp.ClientSession',
                                      method: str, url: str,
                                      **kwargs) -> 'aiohttp.ClientResponse':
    """Send an async request to the API server without retry."""
    # Add API version headers for compatibility (like sync version does)
    if 'headers' not in kwargs:
        kwargs['headers'] = {}
    kwargs['headers'][constants.API_VERSION_HEADER] = str(constants.API_VERSION)
    kwargs['headers'][constants.VERSION_HEADER] = (
        versions.get_local_readable_version())

    try:
        response = await session.request(method, url, **kwargs)

        # Handle server unavailability (503 status) - same as sync version
        if response.status == 503:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ServerTemporarilyUnavailableError(
                    'SkyPilot API server is temporarily unavailable. '
                    'Please try again later.')

        # Set remote API version and version from headers - same as sync version
        remote_api_version = response.headers.get(constants.API_VERSION_HEADER)
        remote_version = response.headers.get(constants.VERSION_HEADER)
        if remote_api_version is not None:
            versions.set_remote_api_version(int(remote_api_version))
        if remote_version is not None:
            versions.set_remote_version(remote_version)

        return response

    except aiohttp.ClientError as e:
        # Convert aiohttp errors to appropriate SkyPilot exceptions
        if isinstance(e, aiohttp.ClientConnectorError):
            raise exceptions.RequestInterruptedError(
                f'Connection failed: {e}') from e
        elif isinstance(e, aiohttp.ClientTimeout):
            raise exceptions.RequestInterruptedError(
                f'Request timeout: {e}') from e
        else:
            raise


def _is_transient_error_async(e: Exception) -> bool:
    """Check if an exception from async request is transient and should be
    retried.

    Mirrors the logic from the sync version's is_transient_error().
    """
    if isinstance(e, aiohttp.ClientError):
        # For response errors, check status code if available
        if isinstance(e, aiohttp.ClientResponseError):
            # Only server error is considered as transient (same as sync
            # version)
            return e.status >= 500
        # Consider connection errors and timeouts as transient
        if isinstance(e, (aiohttp.ClientConnectorError, aiohttp.ClientTimeout)):
            return True

    # Consider server temporarily unavailable as transient
    if isinstance(e, exceptions.ServerTemporarilyUnavailableError):
        return True

    # It is hard to enumerate all other errors that are transient, e.g.
    # broken pipe, connection refused, etc. Instead, it is safer to assume
    # all other errors might be transient since we only retry for 3 times
    # by default. (Same comment as in sync version)
    return True

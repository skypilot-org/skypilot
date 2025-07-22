"""REST API client of SkyPilot API server"""

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
    import requests

else:
    requests = adaptors_common.LazyImport('requests')

F = TypeVar('F', bound=Callable[..., Any])

_RETRY_CONTEXT = contextvars.ContextVar('retry_context', default=None)

_session = requests.Session()
_session.headers[constants.API_VERSION_HEADER] = str(constants.API_VERSION)
_session.headers[constants.VERSION_HEADER] = (
    versions.get_local_readable_version())


class RetryContext:

    def __init__(self):
        self.line_processed = 0


@contextlib.contextmanager
def _retry_in_context():
    token = _RETRY_CONTEXT.set(RetryContext())
    try:
        yield
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
        # It is hard to enumerate all other errors that are transient, e.g.
        # broken pipe, connection refused, etc. Instead, it is safer to assume
        # all other errors might be transient since we only retry for 3 times
        # by default. For permanent errors that we do not know now, we can
        # exclude them here in the future.
        return True

    def decorator(func: F) -> F:

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            backoff = common_utils.Backoff(initial_backoff, max_backoff_factor)
            with _retry_in_context():
                for retry_cnt in range(max_retries):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:  # pylint: disable=broad-except
                        if retry_cnt >= max_retries - 1:
                            # Retries exhausted.
                            raise
                        if not is_transient_error(e):
                            # Permanent error, no need to retry.
                            raise
                        logger.debug(f'Retry {func.__name__} due to {e}, '
                                     f'attempt {retry_cnt + 1}/{max_retries}')
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

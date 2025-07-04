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


def retry_on_server_unavailable(max_wait_seconds: int = 600,
                                initial_backoff: float = 5.0,
                                max_backoff_factor: int = 5):
    """Decorator that retries a function when ServerTemporarilyUnavailableError
    is caught.

    Args:
        max_wait_seconds: Maximum number of seconds to wait for the server to
            be healthy
        initial_backoff: Initial backoff time in seconds
        max_backoff_factor: Maximum backoff factor for exponential backoff

    Notes(dev):
        This decorator is mainly used in two scenarios:
        1. Decorate a Restful API call to make the API call wait for server
           recovery when server is temporarily unavailable. APIs like /api/get
           and /api/stream should not be retried since sending them to a new
           replica of API server will not work.
        2. Decorate a SDK function to make the entire SDK function call get
           retried when /api/get or /logs raises a retryable error. This
           is typically triggered by a graceful upgrade of the API server,
           where the pending requests and logs requests will be interrupted.
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


@contextlib.contextmanager
def _retry_in_context():
    token = _RETRY_CONTEXT.set(RetryContext())
    try:
        yield
    finally:
        _RETRY_CONTEXT.reset(token)


def get_retry_context() -> Optional[RetryContext]:
    return _RETRY_CONTEXT.get()


def handle_server_unavailable(response: 'requests.Response') -> None:
    if response.status_code == 503:
        # TODO(aylei): Hacky, depends on how nginx controller handles backends
        # with no ready endpoints. Should use self-defined status code or header
        # to distinguish retryable server error from general 503 errors.
        with ux_utils.print_exception_no_traceback():
            raise exceptions.ServerTemporarilyUnavailableError(
                'SkyPilot API server is temporarily unavailable. '
                'Please try again later.')


@retry_on_server_unavailable()
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

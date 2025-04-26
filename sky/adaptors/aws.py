"""AWS cloud adaptors

Thread safety notes:

The results of session() is cached by each thread in a thread.local() storage.
This means using their results is completely thread-safe.

We do not cache the resource/client objects, because some credentials may be
automatically rotated, but the cached resource/client object may not refresh the
credential quick enough, which can cause unexpected NoCredentialsError. By
creating the resource/client object from the thread-local session() object every
time, the credentials will be explicitly refreshed.

Calling session(), resource(), and client() is thread-safe, since they use a
lock to protect each object's creation.


This is informed by the following boto3 docs:
- Unlike Resources and Sessions, clients are generally thread-safe.
  https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html
- Resource instances are not thread safe and should not be shared across
  threads or processes
  https://boto3.amazonaws.com/v1/documentation/api/latest/guide/resources.html
- Similar to Resource objects, Session objects are not thread safe and
  should not be shared across threads and processes.
  https://boto3.amazonaws.com/v1/documentation/api/latest/guide/session.html
"""

# pylint: disable=import-outside-toplevel

import logging
import threading
import time
import typing
from typing import Callable, Literal, Optional, TypeVar

from sky.adaptors import common
from sky.utils import annotations
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import boto3
    _ = boto3  # Supress pylint use before assignment error
    import mypy_boto3_ec2
    import mypy_boto3_iam
    import mypy_boto3_s3
    import mypy_boto3_service_quotas
    import mypy_boto3_sts

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for AWS. '
                         'Try pip install "skypilot[aws]"')
boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)
_LAZY_MODULES = (boto3, botocore)

T = TypeVar('T')

logger = logging.getLogger(__name__)
_session_creation_lock = threading.RLock()

version = 1

# Retry 5 times by default for potential credential errors,
# mentioned in
# https://github.com/skypilot-org/skypilot/pull/1988
_MAX_ATTEMPT_FOR_CREATION = 5


class _ThreadLocalLRUCache(threading.local):

    def __init__(self, maxsize=32):
        super().__init__()
        self.cache = annotations.lru_cache(scope='request', maxsize=maxsize)


def _thread_local_lru_cache(maxsize=32):
    # Create thread-local storage for the LRU cache
    local_cache = _ThreadLocalLRUCache(maxsize)
    return local_cache.cache


def _assert_kwargs_builtin_type(kwargs):
    assert all(isinstance(v, (int, float, str)) for v in kwargs.values()), (
        f'kwargs should not contain none built-in types: {kwargs}')


def _create_aws_object(creation_fn_or_cls: Callable[[], T],
                       object_name: str) -> T:
    """Create an AWS object.

    Args:
        creation_fn: The function to create the AWS object.

    Returns:
        The created AWS object.
    """
    attempt = 0
    backoff = common_utils.Backoff()
    while True:
        try:
            # Creating the boto3 objects are not thread-safe,
            # so we add a reentrant lock to synchronize the session creation.
            # Reference: https://github.com/boto/boto3/issues/1592

            # NOTE: we need the lock here to avoid thread-safety issues when
            # creating the resource, because Python module is a shared object,
            # and we are not sure if the code inside 'session()' or
            # 'session().xx()' is thread-safe.
            with _session_creation_lock:
                return creation_fn_or_cls()
        except (botocore_exceptions().CredentialRetrievalError,
                botocore_exceptions().NoCredentialsError) as e:
            attempt += 1
            if attempt >= _MAX_ATTEMPT_FOR_CREATION:
                raise
            time.sleep(backoff.current_backoff())
            logger.info(f'Retry creating AWS {object_name} due to '
                        f'{common_utils.format_exception(e)}.')


# The LRU cache needs to be thread-local to avoid multiple threads sharing the
# same session object, which is not guaranteed to be thread-safe.
@_thread_local_lru_cache()
def session(check_credentials: bool = True):
    """Create an AWS session."""
    s = _create_aws_object(boto3.session.Session, 'session')
    if check_credentials and s.get_credentials() is None:
        # s.get_credentials() can be None if there are actually no credentials,
        # or if we fail to get credentials from IMDS (e.g. due to throttling).
        # Technically, it could be okay to have no credentials, as certain AWS
        # APIs don't actually need them. But afaik everything we use AWS for
        # needs credentials.
        raise botocore_exceptions().NoCredentialsError()
    return s


# New typing overloads can be added as needed.
@typing.overload
def resource(service_name: Literal['ec2'],
             **kwargs) -> 'mypy_boto3_ec2.ServiceResource':
    ...


@typing.overload
def resource(service_name: Literal['s3'],
             **kwargs) -> 'mypy_boto3_s3.ServiceResource':
    ...


@typing.overload
def resource(service_name: Literal['iam'],
             **kwargs) -> 'mypy_boto3_iam.ServiceResource':
    ...


# Avoid caching the resource/client objects. If we are using the assumed role,
# the credentials will be automatically rotated, but the cached resource/client
# object will only refresh the credentials with a fixed 15 minutes interval,
# which can cause unexpected NoCredentialsError. By creating the resource/client
# object every time, the credentials will be explicitly refreshed.
# The creation of the resource/client is relatively fast (around 0.3s), so the
# performance impact is negligible.
# Reference: https://github.com/skypilot-org/skypilot/issues/2697
def resource(service_name: str, **kwargs):
    """Create an AWS resource of a certain service.

    Args:
        service_name: AWS resource name (e.g., 's3').
        kwargs: Other options. We add max_attempts to the kwargs instead of
            using botocore.config.Config() because the latter will generate
            different keys even if the config is the same
    """
    _assert_kwargs_builtin_type(kwargs)

    max_attempts: Optional[int] = kwargs.pop('max_attempts', None)
    if max_attempts is not None:
        config = botocore_config().Config(
            retries={'max_attempts': max_attempts})
        kwargs['config'] = config

    check_credentials = kwargs.pop('check_credentials', True)

    # Need to use the client retrieved from the per-thread session to avoid
    # thread-safety issues (Directly creating the client with boto3.resource()
    # is not thread-safe). Reference: https://stackoverflow.com/a/59635814
    return _create_aws_object(
        lambda: session(check_credentials=check_credentials).resource(
            service_name, **kwargs), 'resource')


# New typing overloads can be added as needed.
@typing.overload
def client(service_name: Literal['s3'], **kwargs) -> 'mypy_boto3_s3.Client':
    pass


@typing.overload
def client(service_name: Literal['ec2'], **kwargs) -> 'mypy_boto3_ec2.Client':
    pass


@typing.overload
def client(service_name: Literal['sts'], **kwargs) -> 'mypy_boto3_sts.Client':
    pass


@typing.overload
def client(service_name: Literal['service-quotas'],
           **kwargs) -> 'mypy_boto3_service_quotas.Client':
    pass


def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    _assert_kwargs_builtin_type(kwargs)

    check_credentials = kwargs.pop('check_credentials', True)

    # Need to use the client retrieved from the per-thread session to avoid
    # thread-safety issues (Directly creating the client with boto3.client() is
    # not thread-safe). Reference: https://stackoverflow.com/a/59635814

    return _create_aws_object(
        lambda: session(check_credentials=check_credentials).client(
            service_name, **kwargs), 'client')


@common.load_lazy_modules(modules=_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions


@common.load_lazy_modules(modules=_LAZY_MODULES)
def botocore_config():
    """AWS botocore exception."""
    from botocore import config
    return config

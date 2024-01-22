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

import functools
import logging
import threading
import time
from typing import Any, Callable

from sky.utils import common_utils

logger = logging.getLogger(__name__)

boto3 = None
botocore = None
_session_creation_lock = threading.RLock()

version = 1

# Retry 5 times by default for potential credential errors,
# mentioned in
# https://github.com/skypilot-org/skypilot/pull/1988
_MAX_ATTEMPT_FOR_CREATION = 5


class _ThreadLocalLRUCache(threading.local):

    def __init__(self, maxsize=32):
        super().__init__()
        self.cache = functools.lru_cache(maxsize=maxsize)


def _thread_local_lru_cache(maxsize=32):
    # Create thread-local storage for the LRU cache
    local_cache = _ThreadLocalLRUCache(maxsize)

    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Use the thread-local LRU cache
            return local_cache.cache(func)(*args, **kwargs)

        return wrapper

    return decorator


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global boto3, botocore
        if boto3 is None or botocore is None:
            try:
                import boto3 as _boto3
                import botocore as _botocore
                boto3 = _boto3
                botocore = _botocore
            except ImportError:
                raise ImportError('Fail to import dependencies for AWS. '
                                  'Try pip install "skypilot[aws]"') from None
        return func(*args, **kwargs)

    return wrapper


def _assert_kwargs_builtin_type(kwargs):
    assert all(isinstance(v, (int, float, str)) for v in kwargs.values()), (
        f'kwargs should not contain none built-in types: {kwargs}')


def _create_aws_object(creation_fn_or_cls: Callable[[], Any],
                       object_name: str) -> Any:
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


@import_package
# The LRU cache needs to be thread-local to avoid multiple threads sharing the
# same session object, which is not guaranteed to be thread-safe.
@_thread_local_lru_cache()
def session():
    """Create an AWS session."""
    return _create_aws_object(boto3.session.Session, 'session')


@import_package
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

    max_attempts = kwargs.pop('max_attempts', None)
    if max_attempts is not None:
        config = botocore_config().Config(
            retries={'max_attempts': max_attempts})
        kwargs['config'] = config
    # Need to use the client retrieved from the per-thread session to avoid
    # thread-safety issues (Directly creating the client with boto3.resource()
    # is not thread-safe). Reference: https://stackoverflow.com/a/59635814
    return _create_aws_object(
        lambda: session().resource(service_name, **kwargs), 'resource')


@import_package
def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    _assert_kwargs_builtin_type(kwargs)
    # Need to use the client retrieved from the per-thread session to avoid
    # thread-safety issues (Directly creating the client with boto3.client() is
    # not thread-safe). Reference: https://stackoverflow.com/a/59635814

    return _create_aws_object(lambda: session().client(service_name, **kwargs),
                              'client')


@import_package
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions


@import_package
def botocore_config():
    """AWS botocore exception."""
    from botocore import config
    return config

"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools
import threading
import time

from sky.utils import common_utils

boto3 = None
botocore = None
_session_creation_lock = threading.RLock()


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
                raise ImportError('Fail to import dependencies for AWS.'
                                  'Try pip install "skypilot[aws]"') from None
        return func(*args, **kwargs)

    return wrapper


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache # pylint: disable=line-too-long
@functools.lru_cache()
@import_package
def session():
    """Create an AWS session."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.

    # Retry 5 times by default for potential credential errors,
    # mentioned in
    # https://github.com/skypilot-org/skypilot/pull/1988
    max_attempts = 5
    attempt = 0
    backoff = common_utils.Backoff()
    err = None
    while attempt < max_attempts:
        try:
            with _session_creation_lock:
                return boto3.session.Session()
        except (botocore_exceptions().CredentialRetrievalError,
                botocore_exceptions().NoCredentialsError) as e:
            time.sleep(backoff.current_backoff())
            err = e
            attempt += 1
    raise err


@import_package
def resource(resource_name: str, **kwargs):
    """Create an AWS resource.

    It is relatively fast to create a resource from the session (<1s), so we
    don't need to cache the resource object.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    # The resource is not thread-safe for boto3, so we add a reentrant lock.
    # Reference: https://boto3.amazonaws.com/v1/documentation/api/latest/guide/resources.html#multithreading-or-multiprocessing-with-resources
    with _session_creation_lock:
        return session().resource(resource_name, **kwargs)


@functools.lru_cache()
def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814
    return session().client(service_name, **kwargs)


@import_package
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions

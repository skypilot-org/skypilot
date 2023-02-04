"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools
import threading

boto3 = None
botocore = None
func_lock = threading.RLock()


def _synchronized(func):
    """Decorator to synchronize a function.

    This decorator is used to synchronize a function across threads.
    """

    @functools.wraps(func)
    def synced_func(*args, **kwargs):
        with func_lock:
            return func(*args, **kwargs)

    return synced_func


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


# Creating the session object is not thread-safe for boto3,
# so we add a reentrant lock to synchronize the session creation.
# Reference: https://github.com/boto/boto3/issues/1592
@functools.lru_cache()
@_synchronized
@import_package
def session():
    """Create an AWS session."""
    # functools.lru_cache() is used to cache the session object
    # for each thread.
    return boto3.session.Session()


@functools.lru_cache()
@import_package
def resource(resource_name: str, **kwargs):
    """Create an AWS resource.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814
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

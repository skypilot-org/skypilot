"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel

from functools import wraps
import threading

boto3 = None
botocore = None
cache = threading.local()


def import_package(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        global boto3, botocore
        if boto3 is None or botocore is None:
            try:
                import boto3 as _boto3
                import boto3.session as _session
                import botocore as _botocore
                boto3 = _boto3
                botocore = _botocore
                # Create session per thread to avoid thread-safety issues.
                # See: https://github.com/boto/boto3/issues/1592 and https://boto3.amazonaws.com/v1/documentation/api/latest/guide/resources.html?highlight=multithreading#multithreading-and-multiprocessing # pylint: disable=line-too-long
                if not hasattr(cache, 'session'):
                    cache.session = _session.Session()
            except ImportError:
                raise ImportError('Fail to import dependencies for AWS.'
                                  'Try pip install "skypilot[aws]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    # Cache clients to avoid creating multiple clients, as the creation of the
    # client is not thread-safe, though the client itself is thread-safe.
    return cache.session.client(service_name, **kwargs)


@import_package
def resource(resource_name: str, **kwargs):
    """Create an AWS resource.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    return cache.session.resource(resource_name, **kwargs)


@import_package
def session():
    """Create an AWS session."""
    return cache.session


@import_package
def exceptions():
    """Client exception."""
    from botocore import exceptions as _exceptions
    return _exceptions

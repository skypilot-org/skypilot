"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel

from functools import wraps

boto3 = None
botocore = None


def import_package(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        global boto3, botocore
        if boto3 is None or botocore is None:
            try:
                import boto3 as _boto3
                import botocore as _botocore
                boto3 = _boto3
                botocore = _botocore
            except ImportError:
                raise ImportError(
                    'Fail to import dependencies for AWS.'
                    'See README for how to install it.') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    return boto3.client(service_name, **kwargs)


@import_package
def resource(resource_name: str, **kwargs):
    """Create an AWS resource.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    return boto3.resource(resource_name, **kwargs)


@import_package
def session():
    """Create an AWS session."""
    return boto3.Session()


@import_package
def client_exception():
    """Client exception."""
    from botocore import exceptions
    return exceptions.ClientError

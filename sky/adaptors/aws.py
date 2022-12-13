"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools

boto3 = None
botocore = None


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
                from ray.autoscaler._private.cli_logger import cli_logger
                cli_logger.configure(verbosity=0)
            except ImportError:
                raise ImportError('Fail to import dependencies for AWS.'
                                  'Try pip install "skypilot[aws]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def resource(resource_name: str, **kwargs):
    """Create an AWS resource.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    from sky.skylet.providers.aws import utils
    region = kwargs.pop('region', None)
    return utils.resource_cache(resource_name, region, **kwargs)


@functools.lru_cache()
@import_package
def session():
    """Create an AWS session."""
    # functools.lru_cache() is used to cache the session object
    # for each thread.
    return boto3.session.Session()


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
    for _ in range(3):
        try:
            return session().client(service_name, **kwargs)
        except KeyError:
            pass


@import_package
def exceptions():
    """Client exception."""
    from botocore import exceptions as _exceptions
    return _exceptions

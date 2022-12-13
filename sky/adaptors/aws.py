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


@functools.lru_cache()
@import_package
def client_cache(name, **kwargs):
    # Adapts from sky.skylet.providers.aws.utils, as clients like 'sts'
    # don't have associated resources, i.e. should not use resource_cache.
    from ray.autoscaler._private import constants
    kwargs.setdefault(
        'config',
        botocore.config.Config(
            retries={'max_attempts': constants.BOTO_MAX_RETRIES}),
    )
    return boto3.client(
        name,
        **kwargs,
    )


@import_package
def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    return client_cache(service_name, **kwargs)


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


@import_package
def session():
    """Create an AWS session."""
    return boto3.session.Session()


@import_package
def exceptions():
    """Client exception."""
    from botocore import exceptions as _exceptions
    return _exceptions

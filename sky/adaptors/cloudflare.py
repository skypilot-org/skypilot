"""Cloudflare cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools
import threading
import os

boto3 = None
botocore = None
_session_creation_lock = threading.RLock()
ACCOUNT_ID_PATH = '~/.cloudflare/accountid'
R2_PROFILE_NAME = 'r2'


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
                raise ImportError('Fail to import dependencies for Cloudflare.'
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
    with _session_creation_lock:
        return boto3.session.Session(profile_name=R2_PROFILE_NAME)


@functools.lru_cache()
@import_package
def resource(resource_name: str, **kwargs):
    """Create a Cloudflare resource.

    Args:
        resource_name: Cloudflare resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    cloudflare_credentials = session_.get_credentials().get_frozen_credentials()
    endpoint = create_endpoint()

    return session_.resource(
        resource_name,
        endpoint_url=endpoint,
        aws_access_key_id=cloudflare_credentials.access_key,
        aws_secret_access_key=cloudflare_credentials.secret_key,
        region_name='auto',
        **kwargs)


@functools.lru_cache()
def client(service_name: str, region):
    """Create an CLOUDFLARE client of a certain service.

    Args:
        service_name: CLOUDFLARE service name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    cloudflare_credentials = session_.get_credentials().get_frozen_credentials()
    endpoint = create_endpoint()

    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=cloudflare_credentials.access_key,
        aws_secret_access_key=cloudflare_credentials.secret_key,
        region_name=region)


@import_package
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions


def create_endpoint():
    """Reads accountid necessary to interact with R2"""

    accountid_path = os.path.expanduser(ACCOUNT_ID_PATH)
    with open(accountid_path, 'r') as f:
        lines = f.readlines()
        accountid = lines[0]

    accountid = accountid.strip()
    endpoint = 'https://' + accountid + '.r2.cloudflarestorage.com'

    return endpoint

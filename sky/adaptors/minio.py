"""Minio cloud adaptors"""
# pylint: disable=import-outside-toplevel

import contextlib
import functools
import threading
import os
from typing import Dict, Optional, Tuple

from sky import skypilot_config
from sky.utils import ux_utils

boto3 = None
botocore = None
_session_creation_lock = threading.RLock()
MINIO_CREDENTIALS_PATH = '~/.minio/minio.credentials'
MINIO_PROFILE_NAME = 'minio'
_INDENT_PREFIX = '    '
NAME = 'Minio'


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
                raise ImportError('Fail to import dependencies for Minio.'
                                  'Try pip install "skypilot[aws]"') from None
        return func(*args, **kwargs)

    return wrapper


@contextlib.contextmanager
def _load_minio_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = MINIO_CREDENTIALS_PATH
    try:
        yield
    finally:
        if prev_credentials_path is None:
            del os.environ['AWS_SHARED_CREDENTIALS_FILE']
        else:
            os.environ['AWS_SHARED_CREDENTIALS_FILE'] = prev_credentials_path


def get_minio_credentials(boto3_session):
    """Gets the MINIO credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the MINIO credentials.
    """
    with _load_minio_credentials_env():
        minio_credentials = boto3_session.get_credentials()
        if minio_credentials is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Minio credentials not found. Run '
                                 '`sky check` to verify credentials are '
                                 'correctly set up.')
        else:
            return minio_credentials.get_frozen_credentials()


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache # pylint: disable=line-too-long
@functools.lru_cache()
@import_package
def session():
    """Create a Minio session."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        with _load_minio_credentials_env():
            session_ = boto3.session.Session(profile_name=MINIO_PROFILE_NAME)
        return session_


@functools.lru_cache()
@import_package
def resource(resource_name: str, **kwargs):
    """Create a Minio resource.

    Args:
        resource_name: Minio resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    minio_credentials = get_minio_credentials(session_)
    endpoint = create_endpoint()

    return session_.resource(
        resource_name,
        endpoint_url=endpoint,
        aws_access_key_id=minio_credentials.access_key,
        aws_secret_access_key=minio_credentials.secret_key,
        region_name='auto',
        **kwargs)


@functools.lru_cache()
def client(service_name: str, region):
    """Create an Minio client of a certain service.

    Args:
        service_name: MINIO service name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    minio_credentials = get_minio_credentials(session_)
    endpoint = create_endpoint()

    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=minio_credentials.access_key,
        aws_secret_access_key=minio_credentials.secret_key,
        region_name=region)


@import_package
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions


def create_endpoint():
    """
    Read minio endpoint from skypilot's config.yaml

    minio:
        endpoint: "http://my-minio-host:9000"

    """
    endpoint = skypilot_config.get_nested(("minio", "endpoint"), None)
    return endpoint


def check_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to Minio.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when both credentials needed for MINIO is set. It is False
        when either of those are not set, which would hint with a
        string on unset credential.
    """

    hints = None
    if not minio_profile_in_aws_cred():
        hints = f'[{MINIO_PROFILE_NAME}] profile is not set in {MINIO_CREDENTIALS_PATH}.'
    if not skypilot_config.get_nested(("minio", "endpoint"), None):
        if hints:
            hints += ' Additionally, '
        else:
            hints = ''
        hints += 'endpoint for MINIO is not set.'

    if hints:
        hints += ' Run the following commands:'
        if not minio_profile_in_aws_cred():
            hints += f'\n{_INDENT_PREFIX}  $ pip install boto3'
            hints += f'\n{_INDENT_PREFIX}  $ AWS_SHARED_CREDENTIALS_FILE={MINIO_CREDENTIALS_PATH} aws configure --profile minio'  # pylint: disable=line-too-long
        if not skypilot_config.get_nested(("minio", "endpoint"), None):
            hints += f'\n{_INDENT_PREFIX}  $ echo {{"minio": {{ "endpoint": "<YOUR MINIO ENDPOINT HERE>" }} }} > ~/.sky/config.yaml'  # pylint: disable=line-too-long
        hints += f'\n{_INDENT_PREFIX}For more info: '
        hints += 'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#minio-minio'  # pylint: disable=line-too-long

    return (False, hints) if hints else (True, hints)


def minio_profile_in_aws_cred() -> bool:
    """Checks if MINIO profile is set in minio credentials"""

    profile_path = os.path.expanduser(MINIO_CREDENTIALS_PATH)
    minio_profile_exists = False
    if os.path.isfile(profile_path):
        with open(profile_path, 'r') as file:
            for line in file:
                if f'[{MINIO_PROFILE_NAME}]' in line:
                    minio_profile_exists = True
                    break
    return minio_profile_exists


def get_credential_file_mounts() -> Dict[str, str]:
    """Checks if minio credential file is set and update if not
       Updates file containing account ID information

    Args:
        file_mounts: stores path to credential files of clouds
    """

    minio_credential_mounts = {
        MINIO_CREDENTIALS_PATH: MINIO_CREDENTIALS_PATH
    }
    return minio_credential_mounts

"""Tigris cloud adaptor for object storage."""

import os
import threading
from typing import Dict, Optional, Tuple

from sky import exceptions
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

TIGRIS_PROFILE_NAME = 'tigris'
ENDPOINT_URL = 'https://t3.storage.dev'
NAME = 'Tigris'
DEFAULT_REGION = 'auto'
_INDENT_PREFIX = '    '

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Tigris. '
                         'Try pip install "skypilot[aws]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


def get_tigris_credentials(boto3_session):
    """Gets the Tigris credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the Tigris
        credentials.
    """
    tigris_credentials = boto3_session.get_credentials()
    if tigris_credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Tigris credentials not found. Run '
                             '`sky check` to verify credentials are '
                             'correctly set up.')
    return tigris_credentials.get_frozen_credentials()


@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session for Tigris."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        session_ = boto3.session.Session(profile_name=TIGRIS_PROFILE_NAME)
        return session_


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create a Tigris resource.

    Args:
        resource_name: Tigris resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    tigris_credentials = get_tigris_credentials(session_)

    return session_.resource(
        resource_name,
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=tigris_credentials.access_key,
        aws_secret_access_key=tigris_credentials.secret_key,
        region_name=DEFAULT_REGION,
        **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str):
    """Create Tigris client of a certain service.

    Args:
        service_name: Tigris service name (e.g., 's3').
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    tigris_credentials = get_tigris_credentials(session_)

    return session_.client(
        service_name,
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=tigris_credentials.access_key,
        aws_secret_access_key=tigris_credentials.secret_key,
        region_name=DEFAULT_REGION,
    )


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions as boto_exceptions
    return boto_exceptions


def check_credentials(
        cloud_capability: cloud.CloudCapability) -> Tuple[bool, Optional[str]]:
    if cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    else:
        raise exceptions.NotSupportedError(
            f'{NAME} does not support {cloud_capability}.')


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to Tigris Object Storage.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when credentials needed for Tigris storage are set.
        It is False when they are not set, which would hint with a
        string on how to set them up.
    """
    hints = None
    if not tigris_profile_in_aws_cred():
        hints = (f'[{TIGRIS_PROFILE_NAME}] profile is not set in '
                 '~/.aws/credentials.')
        hints += ' Run the following commands:'
        hints += f'\n{_INDENT_PREFIX}  $ pip install boto3'
        hints += (f'\n{_INDENT_PREFIX}  $ aws configure --profile '
                  f'{TIGRIS_PROFILE_NAME}')
        hints += (f'\n{_INDENT_PREFIX}    # Enter your Tigris Access Key ID '
                  'and Secret Access Key')
        hints += (f'\n{_INDENT_PREFIX}    # For region, enter: auto')
        hints += f'\n{_INDENT_PREFIX}For more info: '
        hints += 'https://www.tigrisdata.com/docs/sdks/s3/aws-cli/'

    return (False, hints) if hints else (True, hints)


def tigris_profile_in_aws_cred() -> bool:
    """Checks if Tigris profile is set in aws credentials"""
    cred_path = os.path.expanduser('~/.aws/credentials')
    tigris_profile_exists = False
    if os.path.isfile(cred_path):
        with open(cred_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{TIGRIS_PROFILE_NAME}]' in line:
                    tigris_profile_exists = True
                    break
    return tigris_profile_exists


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for Tigris.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files.
    """
    # Tigris uses standard AWS credentials file
    return {'~/.aws/credentials': '~/.aws/credentials'}

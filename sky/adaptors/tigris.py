"""Tigris cloud adaptor for object storage."""

import os
import threading
from typing import Dict, Optional, Tuple

from sky import exceptions
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

# Default profile name
TIGRIS_PROFILE_NAME = 'tigris'
# Environment variable to override the profile name
TIGRIS_PROFILE_ENV = 'TIGRIS_PROFILE'
ENDPOINT_URL = 'https://t3.storage.dev'
NAME = 'Tigris'
DEFAULT_REGION = 'auto'
_INDENT_PREFIX = '    '

# Tigris SDK environment variables
TIGRIS_ACCESS_KEY_ENV = 'TIGRIS_STORAGE_ACCESS_KEY_ID'
TIGRIS_SECRET_KEY_ENV = 'TIGRIS_STORAGE_SECRET_ACCESS_KEY'

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Tigris. '
                         'Try pip install "skypilot[aws]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


def get_tigris_profile() -> Optional[str]:
    """Get the Tigris AWS profile name.

    Returns the profile name from TIGRIS_PROFILE env var if set,
    otherwise returns 'tigris' as the default profile name.
    Returns None if credentials should come from env vars instead.
    """
    return os.environ.get(TIGRIS_PROFILE_ENV, TIGRIS_PROFILE_NAME)


def _get_tigris_sdk_env_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Get Tigris credentials from Tigris SDK environment variables.

    Returns:
        Tuple of (access_key, secret_key) from Tigris SDK env vars, or
        (None, None) if not set.
    """
    access_key = os.environ.get(TIGRIS_ACCESS_KEY_ENV)
    secret_key = os.environ.get(TIGRIS_SECRET_KEY_ENV)
    return (access_key, secret_key)


def get_tigris_credentials(boto3_session):
    """Gets the Tigris credentials from the boto3 session or environment.

    Checks for credentials in the following order:
    1. Tigris SDK environment variables
    2. boto3 session credentials (profile or AWS env vars)

    Args:
        boto3_session: The boto3 session object.
    Returns:
        A tuple of (access_key, secret_key) for Tigris.
    """
    # First check Tigris SDK environment variables
    tigris_access, tigris_secret = _get_tigris_sdk_env_credentials()
    if tigris_access and tigris_secret:
        # Return a simple object with access_key and secret_key attributes
        class TigrisCredentials:

            def __init__(self, access_key, secret_key):
                self.access_key = access_key
                self.secret_key = secret_key

        return TigrisCredentials(tigris_access, tigris_secret)

    # Fall back to boto3 session credentials
    tigris_credentials = boto3_session.get_credentials()
    if tigris_credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Tigris credentials not found. Run '
                             '`sky check` to verify credentials are '
                             'correctly set up.')
    return tigris_credentials.get_frozen_credentials()


@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session for Tigris.

    Uses the profile specified by TIGRIS_PROFILE env var, or 'tigris' by
    default. Falls back to default credentials chain if profile not found.
    """
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        profile_name = get_tigris_profile()
        if profile_name and _profile_exists(profile_name):
            session_ = boto3.session.Session(profile_name=profile_name)
        else:
            # Fall back to default credentials chain (env vars, default profile)
            session_ = boto3.session.Session()
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


def _profile_exists(profile_name: str) -> bool:
    """Check if an AWS profile exists in ~/.aws/credentials."""
    cred_path = os.path.expanduser('~/.aws/credentials')
    if not os.path.isfile(cred_path):
        return False
    with open(cred_path, 'r', encoding='utf-8') as file:
        for line in file:
            if line.strip() == f'[{profile_name}]':
                return True
    return False


def _validate_credentials(access_key: str, secret_key: str) -> bool:
    """Validate Tigris credentials by making an API call.

    Args:
        access_key: The Tigris access key (tid_...).
        secret_key: The Tigris secret key (tsec_...).

    Returns:
        True if the credentials are valid, False otherwise.
    """
    try:
        test_client = boto3.client(
            's3',
            endpoint_url=ENDPOINT_URL,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=DEFAULT_REGION,
        )
        # ListBuckets is a lightweight call to validate credentials
        test_client.list_buckets()
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to Tigris Object Storage.

    Tigris credentials can be configured via:
    1. AWS profile specified by TIGRIS_PROFILE env var (default: 'tigris')
    2. Tigris SDK env vars (TIGRIS_STORAGE_ACCESS_KEY_ID, etc.)

    Credentials are validated by making an API call to Tigris.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when credentials needed for Tigris storage are set.
        It is False when they are not set, which would hint with a
        string on how to set them up.
    """
    profile_name = get_tigris_profile()
    access_key = None
    secret_key = None
    credential_source = None

    # Check for the configured profile
    if profile_name and _profile_exists(profile_name):
        try:
            profile_session = boto3.session.Session(profile_name=profile_name)
            creds = profile_session.get_credentials()
            if creds is not None:
                frozen = creds.get_frozen_credentials()
                access_key = frozen.access_key
                secret_key = frozen.secret_key
                credential_source = f'profile [{profile_name}]'
        except Exception:  # pylint: disable=broad-except
            pass

    # Check for Tigris SDK environment variables
    if access_key is None:
        tigris_access, tigris_secret = _get_tigris_sdk_env_credentials()
        if tigris_access and tigris_secret:
            access_key = tigris_access
            secret_key = tigris_secret
            credential_source = 'Tigris SDK environment variables'

    # Validate credentials if found
    if access_key and secret_key:
        if _validate_credentials(access_key, secret_key):
            return (True, None)
        else:
            # Credentials found but invalid
            hint = (f'Tigris credentials from {credential_source} are invalid. '
                    'Please check your access key and secret key.')
            return (False, hint)

    # No Tigris credentials found, provide hints
    hints = (
        'Tigris credentials not found. Tigris keys start with tid_/tsec_.\n'
        f'{_INDENT_PREFIX}You can configure them via:\n'
        f'{_INDENT_PREFIX}Option 1: Add Tigris keys to an AWS profile:\n'
        f'{_INDENT_PREFIX}  $ pip install "skypilot[tigris]"\n'
        f'{_INDENT_PREFIX}  $ aws configure --profile tigris\n'
        f'{_INDENT_PREFIX}  (Set TIGRIS_PROFILE env var for other profiles)\n'
        f'{_INDENT_PREFIX}Option 2: Set Tigris SDK environment variables:\n'
        f'{_INDENT_PREFIX}  $ export {TIGRIS_ACCESS_KEY_ENV}=tid_...\n'
        f'{_INDENT_PREFIX}  $ export {TIGRIS_SECRET_KEY_ENV}=tsec_...\n'
        f'{_INDENT_PREFIX}Option 3: Set AWS environment variables:\n'
        f'{_INDENT_PREFIX}  $ export AWS_ACCESS_KEY_ID=tid_...\n'
        f'{_INDENT_PREFIX}  $ export AWS_SECRET_ACCESS_KEY=tsec_...\n'
        f'{_INDENT_PREFIX}For more info: '
        'https://www.tigrisdata.com/docs/sdks/s3/aws-cli/')

    return (False, hints)


def tigris_profile_in_aws_cred() -> bool:
    """Checks if the configured Tigris profile exists in AWS credentials."""
    profile_name = get_tigris_profile()
    return profile_name is not None and _profile_exists(profile_name)


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for Tigris.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files.
    """
    # Tigris uses standard AWS credentials file
    return {'~/.aws/credentials': '~/.aws/credentials'}

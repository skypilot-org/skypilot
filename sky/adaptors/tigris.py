"""Tigris cloud adaptor for object storage."""

import configparser
import contextlib
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

# Dedicated credential file paths (separate from ~/.aws/credentials to avoid
# conflicts). These files are uploaded to remote clusters via file mounts.
TIGRIS_CREDENTIALS_PATH = '~/.tigris/credentials'
TIGRIS_CONFIG_PATH = '~/.tigris/config'

# Tigris SDK environment variables
TIGRIS_ACCESS_KEY_ENV = 'TIGRIS_STORAGE_ACCESS_KEY_ID'
TIGRIS_SECRET_KEY_ENV = 'TIGRIS_STORAGE_SECRET_ACCESS_KEY'

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Tigris. '
                         'Try pip install "skypilot[tigris]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


@contextlib.contextmanager
def _load_tigris_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    prev_config_path = os.environ.get('AWS_CONFIG_FILE')
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = TIGRIS_CREDENTIALS_PATH
    os.environ['AWS_CONFIG_FILE'] = TIGRIS_CONFIG_PATH
    try:
        yield
    finally:
        if prev_credentials_path is None:
            del os.environ['AWS_SHARED_CREDENTIALS_FILE']
        else:
            os.environ['AWS_SHARED_CREDENTIALS_FILE'] = prev_credentials_path
        if prev_config_path is None:
            del os.environ['AWS_CONFIG_FILE']
        else:
            os.environ['AWS_CONFIG_FILE'] = prev_config_path


def get_tigris_profile() -> str:
    """Get the Tigris AWS profile name.

    Returns the profile name from TIGRIS_PROFILE env var if set,
    otherwise returns 'tigris' as the default profile name.
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
    """Gets the Tigris credentials from the boto3 session.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with Tigris
        credentials.
    """
    with _load_tigris_credentials_env():
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

    Uses the dedicated Tigris credentials file at ~/.tigris/credentials
    with the 'tigris' profile.
    """
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        with _load_tigris_credentials_env():
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


def _profile_exists_in_aws_cred(profile_name: str) -> bool:
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


def _write_tigris_credential_files(access_key: str, secret_key: str) -> None:
    """Write Tigris credentials to dedicated credential files.

    Writes credentials to ~/.tigris/credentials and config to
    ~/.tigris/config so they can be uploaded to remote clusters
    via file mounts, regardless of how credentials were originally
    configured (AWS profile, Tigris SDK env vars, or AWS env vars).

    Args:
        access_key: The Tigris access key.
        secret_key: The Tigris secret key.
    """
    cred_path = os.path.expanduser(TIGRIS_CREDENTIALS_PATH)
    config_path = os.path.expanduser(TIGRIS_CONFIG_PATH)

    os.makedirs(os.path.dirname(cred_path), exist_ok=True)

    # Write credentials file
    cred_config = configparser.RawConfigParser()
    cred_config[TIGRIS_PROFILE_NAME] = {
        'aws_access_key_id': access_key,
        'aws_secret_access_key': secret_key,
    }
    with open(cred_path, 'w', encoding='utf-8') as f:
        cred_config.write(f)
    os.chmod(cred_path, 0o600)

    # Write config file with endpoint URL.
    # We write this directly instead of using configparser because
    # configparser doesn't handle the nested s3 subsection cleanly.
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(f'[profile {TIGRIS_PROFILE_NAME}]\n')
        f.write(f'endpoint_url = {ENDPOINT_URL}\n')
        f.write('s3 =\n')
        f.write('    addressing_style = virtual\n')


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to Tigris Object Storage.

    Tigris credentials can be configured via:
    1. AWS profile specified by TIGRIS_PROFILE env var (default: 'tigris')
    2. Tigris SDK env vars (TIGRIS_STORAGE_ACCESS_KEY_ID, etc.)

    When valid credentials are found, they are written to a dedicated
    file (~/.tigris/credentials) so they can be propagated to remote
    clusters.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when credentials needed for Tigris storage are set.
        It is False when they are not set, which would hint with a
        string on how to set them up.
    """
    # Fast path: if dedicated credential file already exists, skip
    # the network validation call to avoid slowing down storage operations.
    if tigris_profile_in_credential_file():
        return (True, None)

    profile_name = get_tigris_profile()
    access_key = None
    secret_key = None
    credential_source = None

    # Check for the configured profile in ~/.aws/credentials
    if profile_name and _profile_exists_in_aws_cred(profile_name):
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

    # Check for AWS environment variables as fallback
    if access_key is None:
        aws_access = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret = os.environ.get('AWS_SECRET_ACCESS_KEY')
        if aws_access and aws_secret:
            access_key = aws_access
            secret_key = aws_secret
            credential_source = 'AWS environment variables'

    # Validate credentials if found
    if access_key and secret_key:
        if _validate_credentials(access_key, secret_key):
            # Write credentials to dedicated file so they can be
            # uploaded to remote clusters via file mounts.
            _write_tigris_credential_files(access_key, secret_key)
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


def tigris_profile_in_credential_file() -> bool:
    """Checks if Tigris profile is set in the dedicated credentials file."""
    cred_path = os.path.expanduser(TIGRIS_CREDENTIALS_PATH)
    if not os.path.isfile(cred_path):
        return False
    with open(cred_path, 'r', encoding='utf-8') as file:
        for line in file:
            if f'[{TIGRIS_PROFILE_NAME}]' in line:
                return True
    return False


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for Tigris.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files that will be uploaded to remote clusters.
    """
    return {
        TIGRIS_CREDENTIALS_PATH: TIGRIS_CREDENTIALS_PATH,
        TIGRIS_CONFIG_PATH: TIGRIS_CONFIG_PATH,
    }

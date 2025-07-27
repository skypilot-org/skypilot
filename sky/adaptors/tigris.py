"""Tigris cloud adaptor."""
import contextlib
import os
import threading
from typing import Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Tigris configuration
TIGRIS_PROFILE_NAME = 'tigris'
TIGRIS_CREDENTIALS_PATH = '~/.tigris/credentials'

# Default endpoints
TIGRIS_ENDPOINT_FLY = 'https://fly.storage.tigris.dev'
TIGRIS_ENDPOINT_GLOBAL = 'https://t3.storage.dev'

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Tigris.'
                         'Try pip install "skypilot[aws]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()

NAME = 'Tigris'
SKY_CHECK_NAME = 'Tigris (for object storage)'


@contextlib.contextmanager
def _load_tigris_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    tigris_creds_path = os.path.expanduser(TIGRIS_CREDENTIALS_PATH)
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = tigris_creds_path
    try:
        yield
    finally:
        if prev_credentials_path is None:
            if 'AWS_SHARED_CREDENTIALS_FILE' in os.environ:
                del os.environ['AWS_SHARED_CREDENTIALS_FILE']
        else:
            os.environ['AWS_SHARED_CREDENTIALS_FILE'] = prev_credentials_path


def create_endpoint() -> str:
    """Create the appropriate Tigris endpoint URL."""
    # Check if we're running in Fly.io environment
    if os.environ.get('FLY_APP_NAME'):
        return TIGRIS_ENDPOINT_FLY

    # Check config for custom endpoint
    endpoint = skypilot_config.get_effective_region_config(
        cloud='tigris', region=None, keys=('endpoint_url',), default_value=None)
    if endpoint:
        return endpoint

    return TIGRIS_ENDPOINT_GLOBAL


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


def tigris_profile_in_credentials() -> bool:
    """Checks if Tigris profile is set in credentials file."""
    credentials_path = os.path.expanduser(TIGRIS_CREDENTIALS_PATH)
    tigris_profile_exists = False
    if os.path.isfile(credentials_path):
        with open(credentials_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{TIGRIS_PROFILE_NAME}]' in line:
                    tigris_profile_exists = True
                    break
    return tigris_profile_exists


def get_credential_file_mounts() -> dict:
    """Returns Tigris credential file mounts."""
    file_mounts = {}
    credentials_path = os.path.expanduser(TIGRIS_CREDENTIALS_PATH)
    if os.path.exists(credentials_path):
        file_mounts['~/.tigris/credentials'] = credentials_path
    return file_mounts


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache  # pylint: disable=line-too-long
@annotations.lru_cache(scope='global')
def session():
    """Create a Tigris session."""
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
    endpoint_url = create_endpoint()

    return session_.resource(
        resource_name,
        endpoint_url=endpoint_url,
        config=botocore.config.Config(s3={'addressing_style': 'virtual'}),
        **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str, region: str = 'auto'):
    """Create Tigris client of a certain service.

    Args:
        service_name: Tigris service name (e.g., 's3').
        region: Region for the service.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    endpoint_url = create_endpoint()

    return session_.client(
        service_name,
        region_name=region,
        endpoint_url=endpoint_url,
        config=botocore.config.Config(s3={'addressing_style': 'virtual'}))


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Check if Tigris storage credentials are available.

    Returns:
        Tuple of (is_enabled, error_message).
        is_enabled is True if credentials are properly configured.
        error_message is None if credentials are valid, otherwise contains
        error details.
    """
    hints = None
    credentials_path = os.path.expanduser(TIGRIS_CREDENTIALS_PATH)

    if not tigris_profile_in_credentials():
        hints = f'[{TIGRIS_PROFILE_NAME}] profile is not set in {TIGRIS_CREDENTIALS_PATH}.'

    if not os.path.exists(credentials_path):
        if hints:
            hints += f' {TIGRIS_CREDENTIALS_PATH} does not exist.'
        else:
            hints = f'{TIGRIS_CREDENTIALS_PATH} does not exist.'

    if hints:
        hints += (
            ' Run the following commands:\n'
            '  $ mkdir -p ~/.tigris\n'
            '  $ echo "[tigris]" > ~/.tigris/credentials\n'
            '  $ echo "aws_access_key_id = <tid_your_access_key>" >> ~/.tigris/credentials\n'
            '  $ echo "aws_secret_access_key = <tsec_your_secret_key>" >> ~/.tigris/credentials\n'
            '  $ echo "endpoint_url = https://t3.storage.dev" >> ~/.tigris/credentials\n'
            'For more info: https://docs.skypilot.co/en/latest/getting-started/installation.html#tigris'
        )
        return False, hints

    try:
        # Try to create a session and get credentials
        session_ = session()
        credentials = get_tigris_credentials(session_)

        # Basic validation that credentials exist
        if credentials.access_key and credentials.secret_key:
            return True, None
        else:
            return False, 'Tigris credentials not found or incomplete'

    except (ValueError, RuntimeError, OSError) as e:
        return False, f'Failed to verify Tigris credentials: {str(e)}'


def check_credentials(cloud_capability) -> Tuple[bool, Optional[str]]:
    """Check credentials for the specified capability.

    Args:
        cloud_capability: The capability to check (COMPUTE or STORAGE)

    Returns:
        Tuple of (is_enabled, error_message)
    """

    if cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    elif cloud_capability == cloud.CloudCapability.COMPUTE:
        # Tigris doesn't support compute, only storage
        raise exceptions.NotSupportedError(
            f'{NAME} does not support {cloud_capability.value}.')
    else:
        raise exceptions.NotSupportedError(
            f'{NAME} does not support {cloud_capability}.')

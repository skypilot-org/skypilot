"""Akave cloud adaptor for S3-compatible object storage."""

import configparser
import contextlib
import os
import threading
from typing import Dict, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

AKAVE_PROFILE_NAME = 'akave'
AKAVE_CREDENTIALS_PATH = '~/.akave/akave.credentials'
AKAVE_CONFIG_PATH = '~/.akave/akave.config'
NAME = 'Akave'
DEFAULT_REGION = 'auto'
_INDENT_PREFIX = '    '

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Akave. '
                         'Try pip install "skypilot[akave]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


@contextlib.contextmanager
def _load_akave_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    prev_config_path = os.environ.get('AWS_CONFIG_FILE')
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = AKAVE_CREDENTIALS_PATH
    os.environ['AWS_CONFIG_FILE'] = AKAVE_CONFIG_PATH
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


def get_akave_credentials(boto3_session):
    """Gets the Akave credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the Akave
        credentials.
    """
    with _load_akave_credentials_env():
        akave_credentials = boto3_session.get_credentials()
        if akave_credentials is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Akave credentials not found. Run '
                                 '`sky check` to verify credentials are '
                                 'correctly set up.')
        return akave_credentials.get_frozen_credentials()


@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session for Akave."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    with _session_creation_lock:
        with _load_akave_credentials_env():
            session_ = boto3.session.Session(profile_name=AKAVE_PROFILE_NAME)
        return session_


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create an Akave resource.

    Args:
        resource_name: Akave resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    akave_credentials = get_akave_credentials(session_)
    endpoint = get_endpoint()

    return session_.resource(resource_name,
                             endpoint_url=endpoint,
                             aws_access_key_id=akave_credentials.access_key,
                             aws_secret_access_key=akave_credentials.secret_key,
                             region_name=DEFAULT_REGION,
                             **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str):
    """Create an Akave client of a certain service.

    Args:
        service_name: Akave service name (e.g., 's3').
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    akave_credentials = get_akave_credentials(session_)
    endpoint = get_endpoint()

    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=akave_credentials.access_key,
        aws_secret_access_key=akave_credentials.secret_key,
        region_name=DEFAULT_REGION,
    )


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions as boto_exceptions
    return boto_exceptions


def get_endpoint():
    """Parse the AKAVE_CONFIG_PATH to get the endpoint_url.

    The config file is an AWS-style config file with format:
        [profile akave]
        endpoint_url = https://your-akave-endpoint.example.com

    Returns:
        str: The endpoint URL from the config file.

    Raises:
        ValueError: If the endpoint_url is not configured.
    """
    config_path = os.path.expanduser(AKAVE_CONFIG_PATH)
    if not os.path.isfile(config_path):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'Akave config file not found at {AKAVE_CONFIG_PATH}. '
                'Please configure your Akave endpoint.')

    try:
        config = configparser.ConfigParser()
        config.read(config_path)

        profile_section = f'profile {AKAVE_PROFILE_NAME}'
        if config.has_section(profile_section):
            if config.has_option(profile_section, 'endpoint_url'):
                endpoint = config.get(profile_section, 'endpoint_url')
                return endpoint.strip()
    except (configparser.Error, OSError) as e:
        logger.warning(f'Failed to parse Akave config file: {e}.')

    with ux_utils.print_exception_no_traceback():
        raise ValueError(f'endpoint_url not found in {AKAVE_CONFIG_PATH}. '
                         'Please configure your Akave endpoint.')


def check_credentials(
        cloud_capability: cloud.CloudCapability) -> Tuple[bool, Optional[str]]:
    if cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    else:
        raise exceptions.NotSupportedError(
            f'{NAME} does not support {cloud_capability}.')


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to Akave Object Storage.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when both credentials needed for Akave storage are set.
        It is False when either of those are not set, which would hint with a
        string on unset credential.
    """

    def _profile_exists(file_path: str, header: str) -> bool:
        expanded = os.path.expanduser(file_path)
        if not os.path.isfile(expanded):
            return False
        with open(expanded, 'r', encoding='utf-8') as f:
            return any(header in line for line in f)

    hints = None
    profile_in_cred = _profile_exists(AKAVE_CREDENTIALS_PATH,
                                      f'[{AKAVE_PROFILE_NAME}]')
    profile_in_config = _profile_exists(AKAVE_CONFIG_PATH,
                                        f'[profile {AKAVE_PROFILE_NAME}]')

    if not profile_in_cred:
        hints = (f'[{AKAVE_PROFILE_NAME}] profile is not set in '
                 f'{AKAVE_CREDENTIALS_PATH}.')
    if not profile_in_config:
        if hints:
            hints += ' Additionally, '
        else:
            hints = ''
        hints += (f'[{AKAVE_PROFILE_NAME}] profile is not set in '
                  f'{AKAVE_CONFIG_PATH}.')

    if hints:
        hints += ' Run the following commands:'
        if not profile_in_cred:
            hints += f'\n{_INDENT_PREFIX}  $ pip install "skypilot[akave]"'
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_SHARED_CREDENTIALS_FILE='
                      f'{AKAVE_CREDENTIALS_PATH} aws configure --profile '
                      f'{AKAVE_PROFILE_NAME}')
        if not profile_in_config:
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_CONFIG_FILE='
                      f'{AKAVE_CONFIG_PATH} aws configure set endpoint_url'
                      f' <ENDPOINT_URL> --profile '
                      f'{AKAVE_PROFILE_NAME}')
            hints += (f'\n{_INDENT_PREFIX} For more information, see: '
                      'https://docs.skypilot.co/en/latest/'
                      'getting-started/installation.html#akave')

    return (False, hints) if hints else (True, hints)


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for Akave.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files.
    """
    akave_credential_mounts = {
        AKAVE_CREDENTIALS_PATH: AKAVE_CREDENTIALS_PATH,
        AKAVE_CONFIG_PATH: AKAVE_CONFIG_PATH
    }
    return akave_credential_mounts

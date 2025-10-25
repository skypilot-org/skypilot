"""CoreWeave cloud adaptor."""

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

COREWEAVE_PROFILE_NAME = 'cw'
COREWEAVE_CREDENTIALS_PATH = '~/.coreweave/cw.credentials'
COREWEAVE_CONFIG_PATH = '~/.coreweave/cw.config'
NAME = 'CoreWeave'
DEFAULT_REGION = 'US-EAST-01A'
_DEFAULT_ENDPOINT = 'https://cwobject.com'
_INDENT_PREFIX = '    '

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for CoreWeave.'
                         'Try pip install "skypilot[coreweave]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


@contextlib.contextmanager
def _load_cw_credentials_env():
    """Context manager to temporarily change the AWS credentials file path."""
    prev_credentials_path = os.environ.get('AWS_SHARED_CREDENTIALS_FILE')
    prev_config_path = os.environ.get('AWS_CONFIG_FILE')
    os.environ['AWS_SHARED_CREDENTIALS_FILE'] = COREWEAVE_CREDENTIALS_PATH
    os.environ['AWS_CONFIG_FILE'] = COREWEAVE_CONFIG_PATH
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


def get_coreweave_credentials(boto3_session):
    """Gets the CoreWeave credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the CoreWeave
        credentials.
    """
    with _load_cw_credentials_env():
        coreweave_credentials = boto3_session.get_credentials()
        if coreweave_credentials is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('CoreWeave credentials not found. Run '
                                 '`sky check` to verify credentials are '
                                 'correctly set up.')
        return coreweave_credentials.get_frozen_credentials()


@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session for CoreWeave."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        with _load_cw_credentials_env():
            session_ = boto3.session.Session(
                profile_name=COREWEAVE_PROFILE_NAME)
        return session_


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create a CoreWeave resource.

    Args:
        resource_name: CoreWeave resource name (e.g., 's3').
        kwargs: Other options.
    """
    # Need to use the resource retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.resource() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    coreweave_credentials = get_coreweave_credentials(session_)
    endpoint = get_endpoint()

    return session_.resource(
        resource_name,
        endpoint_url=endpoint,
        aws_access_key_id=coreweave_credentials.access_key,
        aws_secret_access_key=coreweave_credentials.secret_key,
        region_name='auto',
        config=botocore.config.Config(s3={'addressing_style': 'virtual'}),
        **kwargs)


@annotations.lru_cache(scope='global')
def client(service_name: str):
    """Create CoreWeave client of a certain service.

    Args:
        service_name: CoreWeave service name (e.g., 's3').
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814

    session_ = session()
    coreweave_credentials = get_coreweave_credentials(session_)
    endpoint = get_endpoint()

    return session_.client(
        service_name,
        endpoint_url=endpoint,
        aws_access_key_id=coreweave_credentials.access_key,
        aws_secret_access_key=coreweave_credentials.secret_key,
        region_name='auto',
        config=botocore.config.Config(s3={'addressing_style': 'virtual'}),
    )


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions as boto_exceptions
    return boto_exceptions


def get_endpoint():
    """Parse the COREWEAVE_CONFIG_PATH to get the endpoint_url.

    The config file is an AWS-style config file with format:
        [profile cw]
        endpoint_url = https://cwobject.com
        s3 =
            addressing_style = virtual

    Returns:
        str: The endpoint URL from the config file, or the default endpoint
             if the file doesn't exist or doesn't contain the endpoint_url.
    """
    config_path = os.path.expanduser(COREWEAVE_CONFIG_PATH)
    if not os.path.isfile(config_path):
        return _DEFAULT_ENDPOINT

    try:
        config = configparser.ConfigParser()
        config.read(config_path)

        # Try to get endpoint_url from [profile cw] section
        profile_section = f'profile {COREWEAVE_PROFILE_NAME}'
        if config.has_section(profile_section):
            if config.has_option(profile_section, 'endpoint_url'):
                endpoint = config.get(profile_section, 'endpoint_url')
                return endpoint.strip()
    except (configparser.Error, OSError) as e:
        logger.warning(f'Failed to parse CoreWeave config file: {e}. '
                       f'Using default endpoint: {_DEFAULT_ENDPOINT}')

    return _DEFAULT_ENDPOINT


def check_credentials(
        cloud_capability: cloud.CloudCapability) -> Tuple[bool, Optional[str]]:
    if cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    else:
        raise exceptions.NotSupportedError(
            f'{NAME} does not support {cloud_capability}.')


def check_storage_credentials() -> Tuple[bool, Optional[str]]:
    """Checks if the user has access credentials to CoreWeave Object Storage.

    Returns:
        A tuple of a boolean value and a hint message where the bool
        is True when both credentials needed for CoreWeave storage is set.
        It is False when either of those are not set, which would hint with a
        string on unset credential.
    """
    hints = None
    profile_in_cred = coreweave_profile_in_cred()
    profile_in_config = coreweave_profile_in_config()

    if not profile_in_cred:
        hints = (f'[{COREWEAVE_PROFILE_NAME}] profile is not set in '
                 f'{COREWEAVE_CREDENTIALS_PATH}.')
    if not profile_in_config:
        if hints:
            hints += ' Additionally, '
        else:
            hints = ''
        hints += (f'[{COREWEAVE_PROFILE_NAME}] profile is not set in '
                  f'{COREWEAVE_CONFIG_PATH}.')

    if hints:
        hints += ' Run the following commands:'
        if not profile_in_cred:
            hints += f'\n{_INDENT_PREFIX}  $ pip install boto3'
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_SHARED_CREDENTIALS_FILE='
                      f'{COREWEAVE_CREDENTIALS_PATH} aws configure --profile '
                      f'{COREWEAVE_PROFILE_NAME}')
        if not profile_in_config:
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_CONFIG_FILE='
                      f'{COREWEAVE_CONFIG_PATH} aws configure set endpoint_url'
                      f' <ENDPOINT_URL> --profile '
                      f'{COREWEAVE_PROFILE_NAME}')
            hints += (f'\n{_INDENT_PREFIX}  $ AWS_CONFIG_FILE='
                      f'{COREWEAVE_CONFIG_PATH} aws configure set '
                      f's3.addressing_style virtual --profile '
                      f'{COREWEAVE_PROFILE_NAME}')
        hints += f'\n{_INDENT_PREFIX}For more info: '
        hints += 'https://docs.coreweave.com/docs/products/storage/object-storage/get-started-caios'  # pylint: disable=line-too-long

    return (False, hints) if hints else (True, hints)


def coreweave_profile_in_config() -> bool:
    """Checks if CoreWeave profile is set in config"""
    conf_path = os.path.expanduser(COREWEAVE_CONFIG_PATH)
    coreweave_profile_exists = False
    if os.path.isfile(conf_path):
        with open(conf_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {COREWEAVE_PROFILE_NAME}]' in line:
                    coreweave_profile_exists = True
                    break
    return coreweave_profile_exists


def coreweave_profile_in_cred() -> bool:
    """Checks if CoreWeave profile is set in credentials"""
    cred_path = os.path.expanduser(COREWEAVE_CREDENTIALS_PATH)
    coreweave_profile_exists = False
    if os.path.isfile(cred_path):
        with open(cred_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{COREWEAVE_PROFILE_NAME}]' in line:
                    coreweave_profile_exists = True
                    break
    return coreweave_profile_exists


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for CoreWeave.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files.
    """
    coreweave_credential_mounts = {
        COREWEAVE_CREDENTIALS_PATH: COREWEAVE_CREDENTIALS_PATH,
        COREWEAVE_CONFIG_PATH: COREWEAVE_CONFIG_PATH
    }
    return coreweave_credential_mounts

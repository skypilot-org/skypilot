"""CoreWeave cloud adaptor."""
import os
import threading
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common
from sky.clouds import cloud
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

DEFAULT_REGION = 'US-EAST-01A'

COREWEAVE_PROFILE_NAME = 'coreweave'
COREWEAVE_CREDENTIALS_PATH = '~/.aws/credentials'
COREWEAVE_CONFIG_PATH = '~/.aws/config'
_INDENT_PREFIX = '    '
NAME = 'CoreWeave'

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for CoreWeave.'
                         'Try pip install "skypilot[coreweave]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()


def credentials_path() -> str:
    """Get the CoreWeave credentials path."""
    workspace_path = skypilot_config.get_workspace_cloud('coreweave').get(
        'credentials_file_path', None)
    if workspace_path is not None:
        return workspace_path
    return _get_default_credentials_path()


def _get_workspace_credentials_path() -> Optional[str]:
    """Get credentials path if explicitly set in workspace config."""
    workspace_cred_path = skypilot_config.get_workspace_cloud('coreweave').get(
        'credentials_file_path', None)
    return workspace_cred_path


def _get_default_credentials_path() -> str:
    """Get the default credentials path."""
    return '~/.aws/credentials'


def get_coreweave_credentials(boto3_session):
    """Gets the CoreWeave credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials
    """
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
        session_ = boto3.session.Session(profile_name=COREWEAVE_PROFILE_NAME)
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

    return session_.resource(resource_name, **kwargs)


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

    return session_.client(service_name)


@common.load_lazy_modules(_LAZY_MODULES)
def botocore_exceptions():
    """AWS botocore exception."""
    # pylint: disable=import-outside-toplevel
    from botocore import exceptions
    return exceptions


def check_credentials(
        cloud_capability: cloud.CloudCapability) -> Tuple[bool, Optional[str]]:
    if cloud_capability == cloud.CloudCapability.COMPUTE:
        # CoreWeave does not support compute
        return False, f'{NAME} does not support compute instances.'
    elif cloud_capability == cloud.CloudCapability.STORAGE:
        return check_storage_credentials()
    else:
        # Import here to avoid circular import
        from sky import exceptions  # pylint: disable=import-outside-toplevel
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

    if not coreweave_profile_in_aws_cred():
        hints = (f'[{COREWEAVE_PROFILE_NAME}] profile is not set in '
                 f'{COREWEAVE_CREDENTIALS_PATH}.')
    if not coreweave_profile_in_aws_config():
        if hints:
            hints += ' Additionally, '
        else:
            hints = ''
        hints += (f'[profile {COREWEAVE_PROFILE_NAME}] is not set in '
                  f'{COREWEAVE_CONFIG_PATH}.')

    if hints:
        hints += ' Run the following commands:'
        if not coreweave_profile_in_aws_cred():
            hints += f'\n{_INDENT_PREFIX}  $ pip install boto3'
            hints += (f'\n{_INDENT_PREFIX}  $ aws configure --profile '
                      f'{COREWEAVE_PROFILE_NAME}')
        if not coreweave_profile_in_aws_config():
            hints += (f'\n{_INDENT_PREFIX}  $ aws configure set region '
                      f'{DEFAULT_REGION} --profile {COREWEAVE_PROFILE_NAME}')
        hints += (f'\n{_INDENT_PREFIX}For more info: see CoreWeave Object '
                  'Storage documentation')

    return (False, hints) if hints else (True, hints)


def coreweave_profile_in_aws_cred() -> bool:
    """Checks if CoreWeave profile is set in aws credentials"""
    cred_path = os.path.expanduser(COREWEAVE_CREDENTIALS_PATH)
    coreweave_profile_exists = False
    if os.path.isfile(cred_path):
        with open(cred_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{COREWEAVE_PROFILE_NAME}]' in line:
                    coreweave_profile_exists = True
                    break
    return coreweave_profile_exists


def coreweave_profile_in_aws_config() -> bool:
    """Checks if CoreWeave profile is set in aws config"""
    conf_path = os.path.expanduser(COREWEAVE_CONFIG_PATH)
    coreweave_profile_exists = False
    if os.path.isfile(conf_path):
        with open(conf_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {COREWEAVE_PROFILE_NAME}]' in line:
                    coreweave_profile_exists = True
                    break
    return coreweave_profile_exists


def get_credential_file_mounts() -> Dict[str, str]:
    """Returns credential file mounts for CoreWeave.

    Returns:
        Dict[str, str]: A dictionary mapping source paths to destination paths
        for credential files.
    """
    coreweave_credential_mounts = {}

    # Add AWS credentials if CoreWeave profile exists
    if coreweave_profile_in_aws_cred():
        coreweave_credential_mounts[
            COREWEAVE_CREDENTIALS_PATH] = COREWEAVE_CREDENTIALS_PATH

    # Add AWS config if CoreWeave profile exists
    if coreweave_profile_in_aws_config():
        coreweave_credential_mounts[
            COREWEAVE_CONFIG_PATH] = COREWEAVE_CONFIG_PATH

    # Add any additional credential file paths
    for path in get_credential_file_paths():
        if os.path.exists(os.path.expanduser(path)):
            coreweave_credential_mounts[path] = path

    return coreweave_credential_mounts


def get_credential_file_paths() -> List[str]:
    """Get the list of credential file paths based on current configuration."""
    paths = set()

    # Add workspace-specific credentials path if set
    workspace_cred_path = _get_workspace_credentials_path()
    if workspace_cred_path is not None:
        paths.add(workspace_cred_path)
    # Always add default path in case it's needed for fallback
    paths.add(_get_default_credentials_path())

    return list(paths)

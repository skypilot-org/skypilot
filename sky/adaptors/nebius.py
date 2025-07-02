"""Nebius cloud adaptor."""
import os
import threading
from typing import List, Optional

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def tenant_id_path() -> str:
    return '~/.nebius/NEBIUS_TENANT_ID.txt'


def iam_token_path() -> str:
    return '~/.nebius/NEBIUS_IAM_TOKEN.txt'


def credentials_path() -> str:
    workspace_path = skypilot_config.get_workspace_cloud('nebius').get(
        'credentials_file_path', None)
    if workspace_path is not None:
        return workspace_path
    return _get_default_credentials_path()


def _get_workspace_credentials_path() -> Optional[str]:
    """Get credentials path if explicitly set in workspace config."""
    workspace_cred_path = skypilot_config.get_workspace_cloud('nebius').get(
        'credentials_file_path', None)
    return workspace_cred_path


def _get_default_credentials_path() -> str:
    """Get the default credentials path."""
    return '~/.nebius/credentials.json'


DEFAULT_REGION = 'eu-north1'

NEBIUS_PROFILE_NAME = 'nebius'

MAX_RETRIES_TO_DISK_CREATE = 120
MAX_RETRIES_TO_INSTANCE_STOP = 120
MAX_RETRIES_TO_INSTANCE_START = 120
MAX_RETRIES_TO_INSTANCE_READY = 240

MAX_RETRIES_TO_DISK_DELETE = 120
MAX_RETRIES_TO_INSTANCE_WAIT = 120  # Maximum number of retries

POLL_INTERVAL = 5

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Nebius AI Cloud.'
                         'Try pip install "skypilot[nebius]"')

nebius = common.LazyImport(
    'nebius',
    import_error_message=_IMPORT_ERROR_MESSAGE,
    # https://github.com/grpc/grpc/issues/37642 to avoid spam in console
    set_loggers=lambda: os.environ.update({'GRPC_VERBOSITY': 'NONE'}))
boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore, nebius)
_session_creation_lock = threading.RLock()
_INDENT_PREFIX = '    '
NAME = 'Nebius'
SKY_CHECK_NAME = 'Nebius (for Nebius Object Storae)'


def request_error():
    return nebius.aio.service_error.RequestError


def compute():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.compute import v1 as compute_v1
    return compute_v1


def iam():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.iam import v1 as iam_v1
    return iam_v1


def nebius_common():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.common import v1 as common_v1
    return common_v1


def vpc():
    # pylint: disable=import-outside-toplevel
    from nebius.api.nebius.vpc import v1 as vpc_v1
    return vpc_v1


def get_iam_token():
    try:
        with open(os.path.expanduser(iam_token_path()),
                  encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


def is_token_or_cred_file_exist():
    return (os.path.exists(os.path.expanduser(iam_token_path())) or
            os.path.exists(os.path.expanduser(credentials_path())))


def get_tenant_id():
    tenant_id_in_ws_config = skypilot_config.get_workspace_cloud('nebius').get(
        'tenant_id', None)
    if tenant_id_in_ws_config is not None:
        return tenant_id_in_ws_config
    tenant_id_in_config = skypilot_config.get_effective_region_config(
        cloud='nebius', region=None, keys=('tenant_id',), default_value=None)
    if tenant_id_in_config is not None:
        return tenant_id_in_config
    try:
        with open(os.path.expanduser(tenant_id_path()),
                  encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


def sdk():
    """Create the Nebius SDK with the correct credentials.

    The order of priority is:
    1. Credentials file specified in workspace config, if set
    2. IAM token file, if set
    3. Default credentials path
    """
    # 1. Check if credentials path is set in workspace config (highest priority)
    workspace_cred_path = _get_workspace_credentials_path()
    if workspace_cred_path is not None:
        # Check if token is also available and warn
        token = get_iam_token()
        if token is not None:
            logger.warning(
                f'Both workspace credentials file ({workspace_cred_path}) and '
                f'IAM token file ({iam_token_path()}) are available. Using '
                'workspace credentials file.')
        return _sdk(None, workspace_cred_path)

    # 2. Check for IAM token file (second priority)
    token = get_iam_token()
    if token is not None:
        return _sdk(token, None)

    # 3. Fall back to default credentials path (lowest priority)
    default_cred_path = _get_default_credentials_path()
    return _sdk(None, default_cred_path)


@annotations.lru_cache(scope='request')
def _sdk(token: Optional[str], cred_path: Optional[str]):
    # Exactly one of token or cred_path must be provided
    assert (token is None) != (cred_path is None), (token, cred_path)
    if token is not None:
        return nebius.sdk.SDK(credentials=token)
    if cred_path is not None:
        return nebius.sdk.SDK(
            credentials_file_name=os.path.expanduser(cred_path))
    raise ValueError('Either token or credentials file path must be provided')


def get_nebius_credentials(boto3_session):
    """Gets the Nebius credentials from the boto3 session object.

    Args:
        boto3_session: The boto3 session object.
    Returns:
        botocore.credentials.ReadOnlyCredentials object with the R2 credentials.
    """
    nebius_credentials = boto3_session.get_credentials()
    if nebius_credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Nebius credentials not found. Run '
                             '`sky check` to verify credentials are '
                             'correctly set up.')
    return nebius_credentials.get_frozen_credentials()


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache # pylint: disable=line-too-long
@annotations.lru_cache(scope='global')
def session():
    """Create an AWS session."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592
    # However, the session object itself is thread-safe, so we are
    # able to use lru_cache() to cache the session object.
    with _session_creation_lock:
        session_ = boto3.session.Session(profile_name=NEBIUS_PROFILE_NAME)
    return session_


@annotations.lru_cache(scope='global')
def resource(resource_name: str, **kwargs):
    """Create a Nebius resource.

    Args:
        resource_name: Nebius resource name (e.g., 's3').
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
    """Create Nebius client of a certain service.

    Args:
        service_name: Nebius service name (e.g., 's3').
        kwargs: Other options.
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


def get_credential_file_paths() -> List[str]:
    """Get the list of credential file paths based on current configuration."""
    paths = {
        # Always include tenant ID and IAM token paths
        tenant_id_path(),
        iam_token_path(),
    }

    # Add workspace-specific credentials path if set
    workspace_cred_path = _get_workspace_credentials_path()
    if workspace_cred_path is not None:
        paths.add(workspace_cred_path)
    # Always add default path in case it's needed for fallback
    paths.add(_get_default_credentials_path())

    return list(paths)

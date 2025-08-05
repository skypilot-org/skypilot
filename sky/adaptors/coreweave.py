"""CoreWeave cloud adaptor."""
import os
import threading
from typing import List, Optional

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common
from sky.utils import annotations
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

DEFAULT_REGION = 'US-EAST-01A'

COREWEAVE_PROFILE_NAME = 'coreweave'

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for CoreWeave.'
                         'Try pip install "skypilot[coreweave]"')

boto3 = common.LazyImport('boto3', import_error_message=_IMPORT_ERROR_MESSAGE)
botocore = common.LazyImport('botocore',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_LAZY_MODULES = (boto3, botocore)
_session_creation_lock = threading.RLock()
NAME = 'CoreWeave'
SKY_CHECK_NAME = 'CoreWeave (for CoreWeave Object Storage)'


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
        botocore.credentials.ReadOnlyCredentials object with the CoreWeave credentials.
    """
    coreweave_credentials = boto3_session.get_credentials()
    if coreweave_credentials is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('CoreWeave credentials not found. Run '
                             '`sky check` to verify credentials are '
                             'correctly set up.')
    return coreweave_credentials.get_frozen_credentials()


# lru_cache() is thread-safe and it will return the same session object
# for different threads.
# Reference: https://docs.python.org/3/library/functools.html#functools.lru_cache
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

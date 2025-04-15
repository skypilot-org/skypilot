"""Nebius cloud adaptor."""
import os
import threading

from sky.adaptors import common
from sky.utils import annotations
from sky.utils import ux_utils

NEBIUS_TENANT_ID_FILENAME = 'NEBIUS_TENANT_ID.txt'
NEBIUS_IAM_TOKEN_FILENAME = 'NEBIUS_IAM_TOKEN.txt'
NEBIUS_PROJECT_ID_FILENAME = 'NEBIUS_PROJECT_ID.txt'
NEBIUS_CREDENTIALS_FILENAME = 'credentials.json'
NEBIUS_TENANT_ID_PATH = '~/.nebius/' + NEBIUS_TENANT_ID_FILENAME
NEBIUS_IAM_TOKEN_PATH = '~/.nebius/' + NEBIUS_IAM_TOKEN_FILENAME
NEBIUS_PROJECT_ID_PATH = '~/.nebius/' + NEBIUS_PROJECT_ID_FILENAME
NEBIUS_CREDENTIALS_PATH = '~/.nebius/' + NEBIUS_CREDENTIALS_FILENAME

DEFAULT_REGION = 'eu-north1'

NEBIUS_PROFILE_NAME = 'nebius'

MAX_RETRIES_TO_DISK_CREATE = 120
MAX_RETRIES_TO_INSTANCE_STOP = 120
MAX_RETRIES_TO_INSTANCE_START = 120
MAX_RETRIES_TO_INSTANCE_READY = 240

MAX_RETRIES_TO_DISK_DELETE = 120
MAX_RETRIES_TO_INSTANCE_WAIT = 120  # Maximum number of retries

POLL_INTERVAL = 5

_iam_token = None
_sdk = None
_tenant_id = None
_project_id = None

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
    global _iam_token
    if _iam_token is None:
        try:
            with open(os.path.expanduser(NEBIUS_IAM_TOKEN_PATH),
                      encoding='utf-8') as file:
                _iam_token = file.read().strip()
        except FileNotFoundError:
            return None
    return _iam_token


def is_token_or_cred_file_exist():
    return (os.path.exists(os.path.expanduser(NEBIUS_IAM_TOKEN_PATH)) or
            os.path.exists(os.path.expanduser(NEBIUS_CREDENTIALS_PATH)))


def get_project_id():
    global _project_id
    if _project_id is None:
        try:
            with open(os.path.expanduser(NEBIUS_PROJECT_ID_PATH),
                      encoding='utf-8') as file:
                _project_id = file.read().strip()
        except FileNotFoundError:
            return None
    return _project_id


def get_tenant_id():
    global _tenant_id
    if _tenant_id is None:
        try:
            with open(os.path.expanduser(NEBIUS_TENANT_ID_PATH),
                      encoding='utf-8') as file:
                _tenant_id = file.read().strip()
        except FileNotFoundError:
            return None
    return _tenant_id


def sdk():
    global _sdk
    if _sdk is None:
        if get_iam_token() is not None:
            _sdk = nebius.sdk.SDK(credentials=get_iam_token())
            return _sdk
        _sdk = nebius.sdk.SDK(
            credentials_file_name=os.path.expanduser(NEBIUS_CREDENTIALS_PATH))
    return _sdk


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

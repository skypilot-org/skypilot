"""Nebius cloud adaptor."""
import os

from sky.adaptors import common

NEBIUS_TENANT_ID_FILENAME = 'NEBIUS_TENANT_ID.txt'
NEBIUS_IAM_TOKEN_FILENAME = 'NEBIUS_IAM_TOKEN.txt'
NEBIUS_PROJECT_ID_FILENAME = 'NEBIUS_PROJECT_ID.txt'
NEBIUS_TENANT_ID_PATH = '~/.nebius/' + NEBIUS_TENANT_ID_FILENAME
NEBIUS_IAM_TOKEN_PATH = '~/.nebius/' + NEBIUS_IAM_TOKEN_FILENAME
NEBIUS_PROJECT_ID_PATH = '~/.nebius/' + NEBIUS_PROJECT_ID_FILENAME

MAX_RETRIES_TO_DISK_CREATE = 120
MAX_RETRIES_TO_INSTANCE_STOP = 120
MAX_RETRIES_TO_INSTANCE_START = 120
MAX_RETRIES_TO_INSTANCE_READY = 240

MAX_RETRIES_TO_DISK_DELETE = 120
MAX_RETRIES_TO_INSTANCE_WAIT = 120  # Maximum number of retries

POLL_INTERVAL = 5

_iam_token = None
_tenant_id = None
_project_id = None

nebius = common.LazyImport(
    'nebius',
    import_error_message='Failed to import dependencies for Nebius AI Cloud. '
    'Try running: pip install "skypilot[nebius]"',
    # https://github.com/grpc/grpc/issues/37642 to avoid spam in console
    set_loggers=lambda: os.environ.update({'GRPC_VERBOSITY': 'NONE'}))


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
    return nebius.sdk.SDK(credentials=get_iam_token())

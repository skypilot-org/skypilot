"""Nebius cloud adaptor."""
import os

from sky.adaptors import common

NB_TENANT_ID_PATH = 'NB_TENANT_ID.txt'
NEBIUS_IAM_TOKEN_PATH = 'NEBIUS_IAM_TOKEN.txt'

MAX_RETRIES_TO_DISK_CREATE = 120
MAX_RETRIES_TO_INSTANCE_STOP = 120
MAX_RETRIES_TO_INSTANCE_START = 120
MAX_RETRIES_TO_INSTANCE_READY = 240
MAX_RETRIES_TO_DISK_DELETE = 120
MAX_RETRIES_TO_INSTANCE_WAIT = 120  # Maximum number of retries

POLL_INTERVAL = 5

_token = None
# https://github.com/grpc/grpc/issues/37642 to avoid spam in console
os.environ['GRPC_VERBOSITY'] = 'NONE'

nebius = common.LazyImport(
    'nebius',
    import_error_message='Failed to import dependencies for Nebius AI Cloud. '
    'Try running: pip install "skypilot[nebius]"')


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
    global _token
    if _token is None:
        try:
            with open(os.path.expanduser(f'~/.nebius/{NEBIUS_IAM_TOKEN_PATH}'),
                      encoding='utf-8') as file:
                _token = file.read().strip()
        except FileNotFoundError:
            return None
    return _token


def get_tenant_id():
    try:
        with open(os.path.expanduser(f'~/.nebius/{NB_TENANT_ID_PATH}'),
                  encoding='utf-8') as file:
            tenant_id = file.read().strip()
        return tenant_id
    except FileNotFoundError:
        return None


def sdk():
    return nebius.sdk.SDK(credentials=get_iam_token())

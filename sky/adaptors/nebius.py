"""Nebius cloud adaptor."""
import os

from sky.adaptors import common

NB_TENANT_ID_PATH = '~/.nebius/NB_TENANT_ID.txt'
NEBIUS_IAM_TOKEN_PATH = '~/.nebius/NEBIUS_IAM_TOKEN.txt'

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
    with open(os.path.expanduser(NEBIUS_IAM_TOKEN_PATH),
              encoding='utf-8') as file:
        iam_token = file.read().strip()
    return iam_token


def get_tenant_id():
    with open(os.path.expanduser(NB_TENANT_ID_PATH), encoding='utf-8') as file:
        tenant_id = file.read().strip()
    return tenant_id


def sdk(credentials):
    return nebius.sdk.SDK(credentials=credentials)

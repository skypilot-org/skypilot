"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
import functools
import json
import subprocess
import threading

from sky.adaptors import common
from sky.utils.subprocess_utils import run

azure = common.LazyImport(
    'azure',
    import_error_message=('Failed to import dependencies for Azure.'
                          'Try pip install "skypilot[azure]"'))
_LAZY_MODULES = (azure,)

_session_creation_lock = threading.RLock()

# There is no programmatic way to get current account details anymore.
# https://github.com/Azure/azure-sdk-for-python/issues/21561


def _get_account():
    result = run('az account show -o json',
                 stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE)

    if result.returncode != 0:
        error_message = result.stderr.decode()
        print(f'Error executing command: {error_message}')
        raise RuntimeError(
            'Failed to execute az account show -o json command.')

    try:
        return json.loads(result.stdout.decode())
    except json.JSONDecodeError as e:
        error_message = result.stderr.decode()
        print(f'JSON parsing error: {e.msg}')
        raise RuntimeError(
            f'Failed to parse JSON output. Error: {e.msg}\n'
            f'Command Error: {error_message}'
        ) from e


def get_subscription_id() -> str:
    """Get the default subscription id."""
    return _get_account()['id']


def get_current_account_user() -> str:
    """Get the default account user."""
    return _get_account()['user']['name']


@common.load_lazy_modules(modules=_LAZY_MODULES)
def exceptions():
    """Azure exceptions."""
    from azure.core import exceptions as azure_exceptions
    return azure_exceptions


@functools.lru_cache()
@common.load_lazy_modules(modules=_LAZY_MODULES)
def get_client(name: str, subscription_id: str):
    # Sky only supports Azure CLI credential for now.
    # Increase the timeout to fix the Azure get-access-token timeout issue.
    # Tracked in
    # https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110
    from azure.identity import AzureCliCredential
    with _session_creation_lock:
        credential = AzureCliCredential(process_timeout=30)
        if name == 'compute':
            from azure.mgmt.compute import ComputeManagementClient
            return ComputeManagementClient(credential, subscription_id)
        elif name == 'network':
            from azure.mgmt.network import NetworkManagementClient
            return NetworkManagementClient(credential, subscription_id)
        elif name == 'resource':
            from azure.mgmt.resource import ResourceManagementClient
            return ResourceManagementClient(credential, subscription_id)
        else:
            raise ValueError(f'Client not supported: "{name}"')


@common.load_lazy_modules(modules=_LAZY_MODULES)
def create_security_rule(**kwargs):
    from azure.mgmt.network.models import SecurityRule
    return SecurityRule(**kwargs)

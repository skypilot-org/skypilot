"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
import functools
import threading

from sky.adaptors import common

azure = common.LazyImport(
    'azure',
    import_error_message=('Fail to import dependencies for Azure.'
                          'Try pip install "skypilot[azure]"'))
_session_creation_lock = threading.RLock()


def get_subscription_id() -> str:
    """Get the default subscription id."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_subscription_id()


def get_current_account_user() -> str:
    """Get the default account user."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_current_account_user()


def exceptions():
    """Azure exceptions."""
    from azure.core import exceptions as azure_exceptions
    return azure_exceptions


@functools.lru_cache()
def get_client(name: str, subscription_id: str):
    # Sky only supports Azure CLI credential for now.
    # Increase the timeout to fix the Azure get-access-token timeout issue.
    # Tracked in
    # https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110
    from azure.identity import AzureCliCredential
    from azure.mgmt.network import NetworkManagementClient
    from azure.mgmt.resource import ResourceManagementClient
    with _session_creation_lock:
        credential = AzureCliCredential(process_timeout=30)
        if name == 'compute':
            from azure.mgmt.compute import ComputeManagementClient
            return ComputeManagementClient(credential, subscription_id)
        elif name == 'network':
            return NetworkManagementClient(credential, subscription_id)
        elif name == 'resource':
            return ResourceManagementClient(credential, subscription_id)
        else:
            raise ValueError(f'Client not supported: "{name}"')


def create_security_rule(**kwargs):
    from azure.mgmt.network.models import SecurityRule
    return SecurityRule(**kwargs)

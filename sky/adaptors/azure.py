"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
import functools
import threading

from sky.adaptors import common

azure = common.LazyImport(
    'azure',
    import_error_message=('Failed to import dependencies for Azure.'
                          'Try pip install "skypilot[azure]"'))
_session_creation_lock = threading.RLock()


def get_subscription_id() -> str:
    """Get the default subscription id."""
    return azure.common.credentials.get_cli_profile().get_subscription_id()


def get_current_account_user() -> str:
    """Get the default account user."""
    return azure.common.credentials.get_cli_profile().get_current_account_user()


def exceptions():
    """Azure exceptions."""
    return azure.core.exceptions


@functools.lru_cache()
def get_client(name: str, subscription_id: str):
    # Sky only supports Azure CLI credential for now.
    # Increase the timeout to fix the Azure get-access-token timeout issue.
    # Tracked in
    # https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110
    with _session_creation_lock:
        credential = azure.identity.AzureCliCredential(process_timeout=30)
        if name == 'compute':
            return azure.mgmt.compute.ComputeManagementClient(
                credential, subscription_id)
        elif name == 'network':
            return azure.mgmt.network.NetworkManagementClient(
                credential, subscription_id)
        elif name == 'resource':
            return azure.mgmt.resource.ResourceManagementClient(
                credential, subscription_id)
        else:
            raise ValueError(f'Client not supported: "{name}"')


def create_security_rule(**kwargs):
    return azure.mgmt.network.models.SecurityRule(**kwargs)

"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
import functools
import logging
import threading
from typing import Optional

azure = None
_session_creation_lock = threading.RLock()


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global azure
        if azure is None:
            try:
                import azure as _azure  # type: ignore
                azure = _azure
            except ImportError:
                raise ImportError('Fail to import dependencies for Azure.'
                                  'Try pip install "skypilot[azure]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def get_subscription_id() -> str:
    """Get the default subscription id."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_subscription_id()


@import_package
def get_current_account_user() -> str:
    """Get the default account user."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_current_account_user()


@import_package
def core_exception():
    """HttpError exception."""
    from azure.core import exceptions
    return exceptions


@import_package
def http_error_exception():
    """HttpError exception."""
    from azure.core import exceptions
    return exceptions.HttpResponseError


@functools.lru_cache()
@import_package
def get_client(name: str,
               subscription_id: str,
               storage_account_name: Optional[str] = None,
               container_name: Optional[str] = None):
    # Sky only supports Azure CLI credential for now.
    # Increase the timeout to fix the Azure get-access-token timeout issue.
    # Tracked in
    # https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110
    from azure.identity import AzureCliCredential
    from azure.mgmt.network import NetworkManagementClient
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.storage import StorageManagementClient
    from azure.storage.blob import ContainerClient
    with _session_creation_lock:
        credential = AzureCliCredential(process_timeout=30)
        if name == 'compute':
            from azure.mgmt.compute import ComputeManagementClient
            return ComputeManagementClient(credential, subscription_id)
        elif name == 'network':
            return NetworkManagementClient(credential, subscription_id)
        elif name == 'resource':
            return ResourceManagementClient(credential, subscription_id)
        elif name == 'storage':
            return StorageManagementClient(credential, subscription_id)
        elif name == 'container':
            # Suppress noisy logs from Azure SDK when attempting to run 
            # exists() on public container with credentials. Reference:
            # https://github.com/Azure/azure-sdk-for-python/issues/9422
            azure_logger = logging.getLogger('azure')
            azure_logger.setLevel(logging.CRITICAL)

            container_url = (f'https://{storage_account_name}.'
                             f'blob.core.windows.net/{container_name}')
            container_client = ContainerClient.from_container_url(
                container_url,
                credential)
            try:
                container_client.exists()
            except core_exception().ClientAuthenticationError as e:
                # Raised when credential is provided to the public
                # container url. We reattempt without credentials.
                if 'ERROR: AADSTS50020' in e.message:
                    container_client = ContainerClient.from_container_url(
                        container_url)
            return container_client
        else:
            raise ValueError(f'Client not supported: "{name}"')


@import_package
def create_security_rule(**kwargs):
    from azure.mgmt.network.models import SecurityRule
    return SecurityRule(**kwargs)

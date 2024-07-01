"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
import datetime
import functools
import logging
import threading
import time

from sky import exceptions as sky_exceptions
from sky.adaptors import common
from sky.utils import common_utils
from sky.utils import ux_utils

azure = common.LazyImport(
    'azure',
    import_error_message=('Failed to import dependencies for Azure.'
                          'Try pip install "skypilot[azure]"'))
_LAZY_MODULES = (azure,)

_session_creation_lock = threading.RLock()
_MAX_RETRY_FOR_GET_SUBSCRIPTION_ID = 5


@common.load_lazy_modules(modules=_LAZY_MODULES)
@functools.lru_cache()
def get_subscription_id() -> str:
    """Get the default subscription id."""
    from azure.common import credentials
    retry = 0
    backoff = common_utils.Backoff(initial_backoff=0.5, max_backoff_factor=4)
    while True:
        try:
            return credentials.get_cli_profile().get_subscription_id()
        except Exception as e:
            if ('Please run \'az login\' to setup account.' in str(e) and
                    retry < _MAX_RETRY_FOR_GET_SUBSCRIPTION_ID):
                # When there are multiple processes trying to get the
                # subscription id, it may fail with the above error message.
                # Retry will fix the issue.
                retry += 1

                time.sleep(backoff.current_backoff())
                continue
            raise


@common.load_lazy_modules(modules=_LAZY_MODULES)
def get_current_account_user() -> str:
    """Get the default account user."""
    from azure.common import credentials
    return credentials.get_cli_profile().get_current_account_user()


@common.load_lazy_modules(modules=_LAZY_MODULES)
def exceptions():
    """Azure exceptions."""
    from azure.core import exceptions as azure_exceptions
    return azure_exceptions


@common.load_lazy_modules(modules=_LAZY_MODULES)
@functools.lru_cache()
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
        elif name == 'storage':
            from azure.mgmt.storage import StorageManagementClient
            return StorageManagementClient(credential, subscription_id)
        elif name == 'authorization':
            from azure.mgmt.authorization import AuthorizationManagementClient
            return AuthorizationManagementClient(credential, subscription_id)
        elif name == 'graph':
            from msgraph import GraphServiceClient
            return GraphServiceClient(credential)
        elif name == 'container':
            # Currently, there is no way to check if the given endpoint url
            # of a container is private or public. Hence, we approach with
            # try-except block by first assuming the url is a public container.
            # If it turns out to be a private container, an error will be
            # caught and treated as private container.
            # Reference: https://github.com/Azure/azure-sdk-for-python/issues/35770 # pylint: disable=line-too-long
            from azure.storage.blob import ContainerClient
            container_url = kwargs.pop('container_url', None)
            assert container_url is not None, ('Must provide "container_url"'
                                               ' keyword arguments for '
                                               'container client.')
            container_client = ContainerClient.from_container_url(container_url)
            try:
                container_client.exists()
            except exceptions().ClientAuthenticationError:
                # Caught when credential is not provided to the private
                # container url or wrong container name is provided as the
                # public container url. We reattempt with credentials assuming
                # user is using private container with access rights.
                container_client = ContainerClient.from_container_url(
                    container_url, credential)
                try:
                    # Suppress noisy logs from Azure SDK when attempting
                    # to run exists() on public container with credentials.
                    # Reference:
                    # https://github.com/Azure/azure-sdk-for-python/issues/9422 # pylint: disable=line-too-long
                    azure_logger = logging.getLogger('azure')
                    original_level = azure_logger.getEffectiveLevel()
                    azure_logger.setLevel(logging.CRITICAL)
                    container_client.exists()
                    azure_logger.setLevel(original_level)
                except exceptions().ClientAuthenticationError as error:
                    # Caught when user attempted to use private container
                    # without access rights.
                    # Reference:
                    # https://learn.microsoft.com/en-us/troubleshoot/azure/entra/entra-id/app-integration/error-code-aadsts50020-user-account-identity-provider-does-not-exist # pylint: disable=line-too-long
                    if 'ERROR: AADSTS50020' in error.message:
                        with ux_utils.print_exception_no_traceback():
                            raise sky_exceptions.StorageBucketGetError(
                                'Attempted to fetch a non-existant public '
                                'container name: '
                                f'{container_client.container_name}. '
                                'Please check if the name is correct.')
                    else:
                        with ux_utils.print_exception_no_traceback():
                            raise sky_exceptions.StorageBucketGetError(
                                'Failed to retreive the container client '
                                'for the container: '
                                f'{container_client.container_name!r}. '
                                f'Details: '
                                f'{common_utils.format_exception(error, use_bracket=True)}'
                            )

            return container_client
        else:
            raise ValueError(f'Client not supported: "{name}"')


@common.load_lazy_modules(modules=_LAZY_MODULES)
def get_az_container_sas_token(
    storage_account_name: str,
    storage_account_key: str,
    container_name: str,
) -> str:
    """Returns SAS token used to access container.

    Args:
        storage_account_name: str; Name of the storage account
        storage_account_key: str; Access key for the given storage
            account
        container_name: str; The name of the mounting container

    Returns:
        A SAS token with a 1-hour lifespan to access the specified container.
    """
    from azure.storage.blob import ContainerSasPermissions
    from azure.storage.blob import generate_container_sas
    sas_token = generate_container_sas(
        account_name=storage_account_name,
        container_name=container_name,
        account_key=storage_account_key,
        permission=ContainerSasPermissions(read=True,
                                           write=True,
                                           list=True,
                                           create=True),
        expiry=datetime.datetime.now(datetime.timezone.utc) +
        datetime.timedelta(hours=1))
    return sas_token


@common.load_lazy_modules(modules=_LAZY_MODULES)
def get_az_blob_sas_token(storage_account_name: str, storage_account_key: str,
                          container_name: str, blob_name: str) -> str:
    """Returns SAS token used to access a blob.

    Args:
        storage_account_name: str; Name of the storage account
        storage_account_key: str; access key for the given storage
            account
        container_name: str; name of the mounting container
        blob_name: str; path to the blob(file)

    Returns:
        A SAS token with a 1-hour lifespan to access the specified blob.
    """
    from azure.storage.blob import BlobSasPermissions
    from azure.storage.blob import generate_blob_sas
    sas_token = generate_blob_sas(
        account_name=storage_account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=storage_account_key,
        permission=BlobSasPermissions(read=True,
                                      write=True,
                                      list=True,
                                      create=True),
        expiry=datetime.datetime.now(datetime.timezone.utc) +
        datetime.timedelta(hours=1))
    return sas_token


@common.load_lazy_modules(modules=_LAZY_MODULES)
def create_security_rule(**kwargs):
    from azure.mgmt.network.models import SecurityRule
    return SecurityRule(**kwargs)

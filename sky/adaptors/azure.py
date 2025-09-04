"""Azure cli adaptor"""

# pylint: disable=import-outside-toplevel
import asyncio
import json
import contextvars
import datetime
import logging
import os
import threading
import time
from typing import Any, Dict, Optional
import uuid

from sky import exceptions as sky_exceptions
from sky import sky_logging
from sky.adaptors import common
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import common_utils
from sky.utils import ux_utils

azure = common.LazyImport(
    'azure',
    import_error_message=('Failed to import dependencies for Azure.'
                          'Try pip install "skypilot[azure]"'),
    set_loggers=lambda: logging.getLogger('azure.identity').setLevel(logging.
                                                                     ERROR))

# Additional Azure identity imports
try:
    from azure.identity import ClientSecretCredential
except ImportError:
    ClientSecretCredential = None

Client = Any
sky_logger = sky_logging.init_logger(__name__)

_LAZY_MODULES = (azure,)

_session_creation_lock = threading.RLock()
_MAX_RETRY_FOR_GET_SUBSCRIPTION_ID = 5

"""Request-scoped Azure credential context using contextvars.

These propagate across asyncio tasks and are explicitly propagated to new
threads via context_utils.to_thread().
"""
_CTX_AZURE_CREDENTIALS: contextvars.ContextVar[Optional[Dict[str, str]]] = (
    contextvars.ContextVar('azure_credentials', default=None))
_CTX_AZURE_CONFIG_DIR: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar('azure_config_dir', default=None))


def _get_thread_azure_credentials() -> Optional[Dict[str, str]]:
    """Get the Azure service principal credentials from the request context."""
    return _CTX_AZURE_CREDENTIALS.get()


def set_thread_azure_credentials(credentials: Dict[str, str]) -> None:
    """Set the Azure service principal credentials in the request context."""
    _CTX_AZURE_CREDENTIALS.set(credentials)


def clear_thread_azure_credentials() -> None:
    """Clear the Azure service principal credentials from the request context."""
    _CTX_AZURE_CREDENTIALS.set(None)


def _get_thread_azure_config_dir() -> Optional[str]:
    """Get the Azure config directory from the request context."""
    return _CTX_AZURE_CONFIG_DIR.get()


def set_thread_azure_config_dir(config_dir: str) -> None:
    """Set the Azure config directory in the request context."""
    _CTX_AZURE_CONFIG_DIR.set(os.path.expanduser(config_dir))


def clear_thread_azure_config_dir() -> None:
    """Clear the Azure config directory from the request context."""
    _CTX_AZURE_CONFIG_DIR.set(None)


@common.load_lazy_modules(modules=_LAZY_MODULES)
@annotations.lru_cache(scope='request', maxsize=1)
def get_subscription_id() -> str:
    """Get the default subscription id."""
    # First check if we have service principal credentials with subscription_id
    subscription_id = get_subscription_id_from_credentials()
    if subscription_id:
        return subscription_id
    
    # Fall back to Azure CLI
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
    # Check if we have service principal credentials
    service_principal_creds = _get_thread_azure_credentials()
    sky_logger.debug(f"Service principal credentials in get_current_account_user: {service_principal_creds is not None}")
    if service_principal_creds:
        # For service principal, use client_id as the user identifier
        client_id = service_principal_creds.get('client_id', 'unknown')
        sky_logger.debug(f"Using service principal client_id: {client_id}")
        return f"service-principal:{client_id}"

    # Prefer Azure CLI JSON to detect principal type and normalize format.
    # If a custom config dir is set, require success; otherwise, fall back.
    custom_config_dir = _get_thread_azure_config_dir()
    sky_logger.debug(f"Custom config dir: {custom_config_dir}")
    try:
        output = run_azure_cli_with_config('az account show -o json',
                                           custom_config_dir)
        account = json.loads(output)
        user = account.get('user', {}) or {}
        name = user.get('name')
        user_type = (user.get('type') or '').lower()
        if not name:
            raise ValueError('Azure CLI returned empty user.name')
        if user_type == 'serviceprincipal':
            return f'service-principal:{name}'
        return name
    except Exception as e:
        if custom_config_dir:
            # Do not silently switch identity sources when a custom
            # configuration directory is specified.
            raise sky_exceptions.CloudUserIdentityError(
                'Failed to resolve Azure identity from custom AZURE_CONFIG_DIR.\n'
                f'  Config dir: {custom_config_dir}\n'
                f'  Reason: {common_utils.format_exception(e, use_bracket=True)}'
            ) from e

    # Default behavior using Azure SDK if Azure CLI access failed without
    # a custom config dir specified.
    sky_logger.debug("Falling back to Azure CLI profile (SDK)")
    from azure.common import credentials
    return credentials.get_cli_profile().get_current_account_user()


def get_azure_config_dir() -> str:
    """Get the Azure config directory, respecting custom thread-local config."""
    custom_dir = _get_thread_azure_config_dir()
    if custom_dir:
        return custom_dir
    return os.path.expanduser('~/.azure')


def run_azure_cli_with_config(cmd: str, azure_config_dir: Optional[str] = None) -> str:
    """Run Azure CLI command with custom config directory if specified."""
    import subprocess
    
    if azure_config_dir is None:
        azure_config_dir = get_azure_config_dir()
    
    # Use AZURE_CONFIG_DIR env var for this specific command only
    env = os.environ.copy()
    env['AZURE_CONFIG_DIR'] = azure_config_dir
    
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE,
                          env=env)
    return proc.stdout.decode('ascii')


@common.load_lazy_modules(modules=_LAZY_MODULES)
def exceptions():
    """Azure exceptions."""
    from azure.core import exceptions as azure_exceptions
    return azure_exceptions


@annotations.lru_cache(scope='global')
@common.load_lazy_modules(modules=_LAZY_MODULES)
def azure_mgmt_models(name: str):
    if name == 'compute':
        from azure.mgmt.compute import models
        return models
    elif name == 'network':
        from azure.mgmt.network import models
        return models


# We should keep the order of the decorators having 'lru_cache' followed
# by 'load_lazy_modules' as we need to make sure a caller can call
# 'get_client.cache_clear', which is a function provided by 'lru_cache'
@annotations.lru_cache(scope='global')
@common.load_lazy_modules(modules=_LAZY_MODULES)
def get_client(name: str,
               subscription_id: Optional[str] = None,
               **kwargs) -> Client:
    """Creates and returns an Azure client for the specified service.

    Args:
        name: The type of Azure client to create.
        subscription_id: The Azure subscription ID. Defaults to None.

    Returns:
        An instance of the specified Azure client.

    Raises:
        NonExistentStorageAccountError: When storage account provided
            either through config.yaml or local db does not exist under
            user's subscription ID.
        StorageBucketGetError: If there is an error retrieving the container
            client or if a non-existent public container is specified.
        ValueError: If an unsupported client type is specified.
        TimeoutError: If unable to get the container client within the
            specified time.
    """
    from azure import identity
    
    # First try to get service principal credential
    credential = get_azure_service_principal_credential()
    sky_logger.debug(f"Service principal credential in get_client: {credential is not None}")

    if credential is None:
        # Fall back to Azure CLI credential
        # Increase the timeout to fix the Azure get-access-token timeout issue.
        # Tracked in
        # https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110
        sky_logger.debug("Falling back to Azure CLI credential")
        with _session_creation_lock:
            credential = identity.AzureCliCredential(process_timeout=30)
    
    if name == 'compute':
        from azure.mgmt import compute
        return compute.ComputeManagementClient(credential, subscription_id)
    elif name == 'network':
        from azure.mgmt import network
        return network.NetworkManagementClient(credential, subscription_id)
    elif name == 'resource':
        from azure.mgmt import resource
        return resource.ResourceManagementClient(credential,
                                                 subscription_id)
    elif name == 'storage':
        from azure.mgmt import storage
        return storage.StorageManagementClient(credential, subscription_id)
    elif name == 'authorization':
        from azure.mgmt import authorization
        return authorization.AuthorizationManagementClient(
            credential, subscription_id)
    elif name == 'msi':
        from azure.mgmt import msi
        return msi.ManagedServiceIdentityClient(credential, subscription_id)
    elif name == 'graph':
        import msgraph
        return msgraph.GraphServiceClient(credential)
    elif name == 'container':
            # There is no direct way to check if a container URL is public or
            # private. Attempting to access a private container without
            # credentials or a public container with credentials throws an
            # error. Therefore, we use a try-except block, first assuming the
            # URL is for a public container. If an error occurs, we retry with
            # credentials, assuming it's a private container.
            # Reference: https://github.com/Azure/azure-sdk-for-python/issues/35770  # pylint: disable=line-too-long
            # Note: Checking a private container without credentials is
            # faster (~0.2s) than checking a public container with
            # credentials (~90s).
            from azure.mgmt import storage
            from azure.storage import blob
            container_url = kwargs.pop('container_url', None)
            assert container_url is not None, ('Must provide container_url'
                                               ' keyword arguments for '
                                               'container client.')
            storage_account_name = kwargs.pop('storage_account_name', None)
            assert storage_account_name is not None, ('Must provide '
                                                      'storage_account_name '
                                                      'keyword arguments for '
                                                      'container client.')

            # Check if the given storage account exists. This separate check
            # is necessary as running container_client.exists() with container
            # url on non-existent storage account errors out after long lag(~90s)
            storage_client = storage.StorageManagementClient(
                credential, subscription_id)
            storage_account_availability = (
                storage_client.storage_accounts.check_name_availability(
                    {'name': storage_account_name}))
            if storage_account_availability.name_available:
                with ux_utils.print_exception_no_traceback():
                    raise sky_exceptions.NonExistentStorageAccountError(
                        f'The storage account {storage_account_name!r} does '
                        'not exist. Please check if the name is correct.')

            # First, assume the URL is from a public container.
            container_client = blob.ContainerClient.from_container_url(
                container_url)
            try:
                container_client.exists()
                return container_client
            except exceptions().ClientAuthenticationError:
                pass

            # If the URL is not for a public container, assume it's private
            # and retry with credentials.
            start_time = time.time()
            role_assigned = False

            while (time.time() - start_time <
                   constants.WAIT_FOR_STORAGE_ACCOUNT_ROLE_ASSIGNMENT):
                container_client = blob.ContainerClient.from_container_url(
                    container_url, credential)
                try:
                    # Suppress noisy logs from Azure SDK when attempting
                    # to run exists() on private container without access.
                    # Reference:
                    # https://github.com/Azure/azure-sdk-for-python/issues/9422
                    azure_logger = logging.getLogger('azure')
                    original_level = azure_logger.getEffectiveLevel()
                    azure_logger.setLevel(logging.CRITICAL)
                    container_client.exists()
                    azure_logger.setLevel(original_level)
                    return container_client
                except exceptions().ClientAuthenticationError as e:
                    # Caught when user attempted to use private container
                    # without access rights. Raised error is handled at the
                    # upstream.
                    # Reference: https://learn.microsoft.com/en-us/troubleshoot/azure/entra/entra-id/app-integration/error-code-aadsts50020-user-account-identity-provider-does-not-exist # pylint: disable=line-too-long
                    if 'ERROR: AADSTS50020' in str(e):
                        with ux_utils.print_exception_no_traceback():
                            raise e
                    with ux_utils.print_exception_no_traceback():
                        raise sky_exceptions.StorageBucketGetError(
                            'Failed to retreive the container client for the '
                            f'container {container_client.container_name!r}. '
                            f'Details: '
                            f'{common_utils.format_exception(e, use_bracket=True)}'
                        )
                except exceptions().HttpResponseError as e:
                    # Handle case where user lacks sufficient IAM role for
                    # a private container in the same subscription. Attempt to
                    # assign appropriate role to current user.
                    if 'AuthorizationPermissionMismatch' in str(e):
                        if not role_assigned:
                            # resource_group_name is not None only for private
                            # containers with user access.
                            resource_group_name = kwargs.pop(
                                'resource_group_name', None)
                            assert resource_group_name is not None, (
                                'Must provide resource_group_name keyword '
                                'arguments for container client.')
                            sky_logger.info(
                                'Failed to check the existence of the '
                                f'container {container_url!r} due to '
                                'insufficient IAM role for storage '
                                f'account {storage_account_name!r}.')
                            assign_storage_account_iam_role(
                                storage_account_name=storage_account_name,
                                resource_group_name=resource_group_name)
                            role_assigned = True
                        else:
                            sky_logger.info(
                                'Waiting due to the propagation delay of IAM '
                                'role assignment to the storage account '
                                f'{storage_account_name!r}.')
                            time.sleep(
                                constants.RETRY_INTERVAL_AFTER_ROLE_ASSIGNMENT)
                        continue
                    with ux_utils.print_exception_no_traceback():
                        raise sky_exceptions.StorageBucketGetError(
                            'Failed to retreive the container client for the '
                            f'container {container_client.container_name!r}. '
                            f'Details: '
                            f'{common_utils.format_exception(e, use_bracket=True)}'
                        )
            else:
                raise TimeoutError(
                    'Failed to get the container client within '
                    f'{constants.WAIT_FOR_STORAGE_ACCOUNT_ROLE_ASSIGNMENT}'
                    ' seconds.')
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
        storage_account_name: Name of the storage account
        storage_account_key: Access key for the given storage account
        container_name: The name of the mounting container

    Returns:
        An SAS token with a 1-hour lifespan to access the specified container.
    """
    from azure.storage import blob
    sas_token = blob.generate_container_sas(
        account_name=storage_account_name,
        container_name=container_name,
        account_key=storage_account_key,
        permission=blob.ContainerSasPermissions(read=True,
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
        storage_account_name: Name of the storage account
        storage_account_key: access key for the given storage
            account
        container_name: name of the mounting container
        blob_name: path to the blob(file)

    Returns:
        A SAS token with a 1-hour lifespan to access the specified blob.
    """
    from azure.storage import blob
    sas_token = blob.generate_blob_sas(
        account_name=storage_account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=storage_account_key,
        permission=blob.BlobSasPermissions(read=True,
                                           write=True,
                                           list=True,
                                           create=True),
        expiry=datetime.datetime.now(datetime.timezone.utc) +
        datetime.timedelta(hours=1))
    return sas_token


def assign_storage_account_iam_role(
        storage_account_name: str,
        storage_account_id: Optional[str] = None,
        resource_group_name: Optional[str] = None) -> None:
    """Assigns the Storage Blob Data Owner role to a storage account.

    This function retrieves the current user's object ID, then assigns the
    Storage Blob Data Owner role to that user for the specified storage
    account. If the role is already assigned, the function will return without
    making changes.

    Args:
        storage_account_name: The name of the storage account.
        storage_account_id: The ID of the storage account. If not provided,
          it will be determined using the storage account name.
        resource_group_name: Name of the resource group the
            passed storage account belongs to.

    Raises:
        StorageBucketCreateError: If there is an error assigning the role
          to the storage account.
    """
    subscription_id = get_subscription_id()
    authorization_client = get_client('authorization', subscription_id)
    graph_client = get_client('graph')

    # Obtaining user's object ID to assign role.
    # Reference: https://github.com/Azure/azure-sdk-for-python/issues/35573 # pylint: disable=line-too-long
    async def get_object_id() -> str:
        httpx_logger = logging.getLogger('httpx')
        original_level = httpx_logger.getEffectiveLevel()
        # silencing the INFO level response log from httpx request
        httpx_logger.setLevel(logging.WARNING)
        user = await graph_client.users.with_url(
            'https://graph.microsoft.com/v1.0/me').get()
        httpx_logger.setLevel(original_level)
        object_id = str(user.additional_data['id'])
        return object_id

    # Create a new event loop if none exists
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    object_id = loop.run_until_complete(get_object_id())

    # Defintion ID of Storage Blob Data Owner role.
    # Reference: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage#storage-blob-data-owner # pylint: disable=line-too-long
    storage_blob_data_owner_role_id = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
    role_definition_id = ('/subscriptions'
                          f'/{subscription_id}'
                          '/providers/Microsoft.Authorization'
                          '/roleDefinitions'
                          f'/{storage_blob_data_owner_role_id}')

    # Obtain storage account ID to assign role if not provided.
    if storage_account_id is None:
        assert resource_group_name is not None, ('resource_group_name should '
                                                 'be provided if '
                                                 'storage_account_id is not.')
        storage_client = get_client('storage', subscription_id)
        storage_account = storage_client.storage_accounts.get_properties(
            resource_group_name, storage_account_name)
        storage_account_id = storage_account.id

    role_assignment_failure_error_msg = (
        constants.ROLE_ASSIGNMENT_FAILURE_ERROR_MSG.format(
            storage_account_name=storage_account_name))
    try:
        authorization_client.role_assignments.create(
            scope=storage_account_id,
            role_assignment_name=uuid.uuid4(),
            parameters={
                'properties': {
                    'principalId': object_id,
                    'principalType': 'User',
                    'roleDefinitionId': role_definition_id,
                }
            },
        )
        sky_logger.info('Assigned Storage Blob Data Owner role to your '
                        f'account on storage account {storage_account_name!r}.')
        return
    except exceptions().ResourceExistsError as e:
        # Return if the storage account already has been assigned
        # the role.
        if 'RoleAssignmentExists' in str(e):
            return
        else:
            with ux_utils.print_exception_no_traceback():
                raise sky_exceptions.StorageBucketCreateError(
                    f'{role_assignment_failure_error_msg}'
                    f'Details: {common_utils.format_exception(e, use_bracket=True)}'
                )
    except exceptions().HttpResponseError as e:
        if 'AuthorizationFailed' in str(e):
            with ux_utils.print_exception_no_traceback():
                raise sky_exceptions.StorageBucketCreateError(
                    f'{role_assignment_failure_error_msg}'
                    'Please check to see if you have the authorization'
                    ' "Microsoft.Authorization/roleAssignments/write" '
                    'to assign the role to the newly created storage '
                    'account.')
        else:
            with ux_utils.print_exception_no_traceback():
                raise sky_exceptions.StorageBucketCreateError(
                    f'{role_assignment_failure_error_msg}'
                    f'Details: {common_utils.format_exception(e, use_bracket=True)}'
                )


def get_az_resource_group(
        storage_account_name: str,
        storage_client: Optional[Client] = None) -> Optional[str]:
    """Returns the resource group name the given storage account belongs to.

    Args:
        storage_account_name: Name of the storage account
        storage_client: Client object facing storage

    Returns:
        Name of the resource group the given storage account belongs to, or
        None if not found.
    """
    if storage_client is None:
        subscription_id = get_subscription_id()
        storage_client = get_client('storage', subscription_id)
    for account in storage_client.storage_accounts.list():
        if account.name == storage_account_name:
            # Extract the resource group name from the account ID
            # An example of account.id would be the following:
            # /subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.Storage/storageAccounts/{container_name} # pylint: disable=line-too-long
            split_account_id = account.id.split('/')
            assert len(split_account_id) == 9
            resource_group_name = split_account_id[4]
            return resource_group_name
    # resource group cannot be found when using container not created
    # under the user's subscription id, i.e. public container, or
    # private containers not belonging to the user or when the storage account
    # does not exist.
    return None


@common.load_lazy_modules(modules=_LAZY_MODULES)
def create_security_rule(**kwargs):
    from azure.mgmt.network import models
    return models.SecurityRule(**kwargs)


@common.load_lazy_modules(modules=_LAZY_MODULES)
def deployment_mode():
    """Azure deployment mode."""
    from azure.mgmt.resource.resources.models import DeploymentMode
    return DeploymentMode


def create_azure_service_principal_credential(credentials: Dict[str, str]):
    """Create Azure ClientSecretCredential from service principal credentials."""
    if ClientSecretCredential is None:
        raise ImportError("azure.identity.ClientSecretCredential not available. "
                         "Try pip install 'skypilot[azure]'")
    
    return ClientSecretCredential(
        tenant_id=credentials['tenant_id'],
        client_id=credentials['client_id'],
        client_secret=credentials['client_secret']
    )


def get_azure_service_principal_credential():
    """Get Azure credential from thread-local service principal credentials."""
    credentials = _get_thread_azure_credentials()
    if credentials is None:
        return None
    
    return create_azure_service_principal_credential(credentials)


def get_subscription_id_from_credentials() -> Optional[str]:
    """Get subscription ID from thread-local credentials or environment."""
    credentials = _get_thread_azure_credentials()
    if credentials and 'subscription_id' in credentials:
        return credentials['subscription_id']
    return None

"""Azure configuration bootstrapping.

Creates the resource group and deploys the configuration template to Azure for
a cluster to be launched.
"""
import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any, Callable, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.adaptors import azure
from sky.provision import common
from sky.provision import constants
from sky.utils import common_utils
from sky.utils import schemas

logger = sky_logging.init_logger(__name__)

UNIQUE_ID_LEN = 4
_RESOURCE_GROUP_WAIT_FOR_DELETION_TIMEOUT = 480  # 8 minutes
_CLUSTER_ID = '{cluster_name_on_cloud}-{unique_id}'


def get_azure_sdk_function(client: Any, function_name: str) -> Callable:
    """Retrieve a callable function from Azure SDK client object.

    Newer versions of the various client SDKs renamed function names to
    have a begin_ prefix. This function supports both the old and new
    versions of the SDK by first trying the old name and falling back to
    the prefixed new name.
    """
    func = getattr(client, function_name,
                   getattr(client, f'begin_{function_name}', None))
    if func is None:
        raise AttributeError(
            f'{client.__name__!r} object has no {function_name} or '
            f'begin_{function_name} attribute')
    return func


def get_cluster_id_and_nsg_name(resource_group: str,
                                cluster_name_on_cloud: str) -> Tuple[str, str]:
    hasher = hashlib.md5(resource_group.encode('utf-8'))
    unique_id = hasher.hexdigest()[:UNIQUE_ID_LEN]
    # We use the cluster name + resource group hash as the
    # unique ID for the cluster, as we need to make sure that
    # the deployments have unique names during failover.
    cluster_id = _CLUSTER_ID.format(cluster_name_on_cloud=cluster_name_on_cloud,
                                    unique_id=unique_id)
    nsg_name = f'sky-{cluster_id}-nsg'
    return cluster_id, nsg_name


@common.log_function_start_end
def bootstrap_instances(
        region: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """See sky/provision/__init__.py"""
    # TODO: use new azure sdk instead of ARM deployment.
    del region  # unused
    provider_config = config.provider_config
    subscription_id = provider_config.get('subscription_id')
    if subscription_id is None:
        subscription_id = azure.get_subscription_id()
    # Increase the timeout to fix the Azure get-access-token (used by ray azure
    # node_provider) timeout issue.
    # Tracked in https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110 # pylint: disable=line-too-long
    resource_client = azure.get_client('resource', subscription_id)
    provider_config['subscription_id'] = subscription_id
    logger.info(f'Using subscription id: {subscription_id}')

    assert (
        'resource_group'
        in provider_config), 'Provider config must include resource_group field'
    resource_group = provider_config['resource_group']

    assert ('location'
            in provider_config), 'Provider config must include location field'
    params = {'location': provider_config['location']}

    assert ('use_external_resource_group'
            in provider_config), ('Provider config must include '
                                  'use_external_resource_group field')
    use_external_resource_group = provider_config['use_external_resource_group']

    if 'tags' in provider_config:
        params['tags'] = provider_config['tags']

    # When resource group is user specified, it already exists in certain
    # region.
    if not use_external_resource_group:
        logger.info(f'Creating/Updating resource group: {resource_group}')
        rg_create_or_update = get_azure_sdk_function(
            client=resource_client.resource_groups,
            function_name='create_or_update')
        rg_creation_start = time.time()
        retry = 0
        while (time.time() - rg_creation_start <
               _RESOURCE_GROUP_WAIT_FOR_DELETION_TIMEOUT):
            try:
                rg_create_or_update(resource_group_name=resource_group,
                                    parameters=params)
                break
            except azure.exceptions().ResourceExistsError as e:
                if 'ResourceGroupBeingDeleted' in str(e):
                    if retry % 5 == 0:
                        logger.info(
                            f'Azure resource group {resource_group} of a '
                            'recent terminated cluster '
                            f'{cluster_name_on_cloud} is being deleted. It can'
                            ' only be provisioned after it is fully deleted. '
                            'Waiting...')
                    time.sleep(1)
                    retry += 1
                    continue
                raise
            except azure.exceptions().ClientAuthenticationError as e:
                message = (
                    'Failed to authenticate with Azure. Please check your '
                    'Azure credentials. ClientAuthenticationError: '
                    f'{common_utils.format_exception(e)}').replace('\n', ' ')
                logger.error(message)
                raise exceptions.NoClusterLaunchedError(message) from e
        else:
            message = (
                f'Timed out waiting for resource group {resource_group} to be '
                'deleted.')
            logger.error(message)
            raise TimeoutError(message)

    # Resolve custom managed identity before ARM deployment.
    remote_identity = provider_config.get('remote_identity')
    custom_msi_id = _resolve_custom_managed_identity(remote_identity,
                                                     subscription_id,
                                                     resource_group)

    # Resolve custom VNet before ARM deployment.
    vpc_name = provider_config.get('vpc_name')
    custom_vnet = _resolve_custom_vnet(vpc_name, subscription_id,
                                       resource_group,
                                       provider_config['location'])

    # load the template file
    current_path = Path(__file__).parent
    template_path = current_path.joinpath('azure-config-template.json')
    with open(template_path, 'r', encoding='utf-8') as template_fp:
        template = json.load(template_fp)

    # When using a custom MSI, remove MSI and role assignment resources
    # from the ARM template. The user may not have permissions to create
    # role assignments (Microsoft.Authorization/roleAssignments/write).
    if custom_msi_id is not None:
        _remove_msi_resources_from_template(template)

    # When using a custom VNet, remove networking resources from the
    # ARM template since we use the existing VNet/Subnet.
    if custom_vnet is not None:
        _remove_network_resources_from_template(template)

    logger.info(f'Using cluster name: {cluster_name_on_cloud}')

    cluster_id, nsg_name = get_cluster_id_and_nsg_name(
        resource_group=provider_config['resource_group'],
        cluster_name_on_cloud=cluster_name_on_cloud)
    subnet_mask = provider_config.get('subnet_mask')
    if subnet_mask is None:
        # choose a random subnet, skipping most common value of 0
        random.seed(cluster_id)
        subnet_mask = f'10.{random.randint(1, 254)}.0.0/16'
    logger.info(f'Using subnet mask: {subnet_mask}')

    parameters = {
        'properties': {
            'mode': azure.deployment_mode().incremental,
            'template': template,
            'parameters': {
                'subnet': {
                    'value': subnet_mask
                },
                'clusterId': {
                    'value': cluster_id
                },
                'nsgName': {
                    'value': nsg_name
                },
                'location': {
                    'value': params['location']
                }
            },
        }
    }

    # Skip creating or updating the deployment if the deployment already exists
    # and the cluster name is the same.
    get_deployment = get_azure_sdk_function(client=resource_client.deployments,
                                            function_name='get')
    deployment_exists = False
    if use_external_resource_group:
        deployment_name = (
            constants.EXTERNAL_RG_BOOTSTRAP_DEPLOYMENT_NAME.format(
                cluster_name_on_cloud=cluster_name_on_cloud))
        deployment_list = [deployment_name]
    else:
        deployment_name = constants.DEPLOYMENT_NAME
        deployment_list = [
            constants.DEPLOYMENT_NAME, constants.LEGACY_DEPLOYMENT_NAME
        ]

    for deploy_name in deployment_list:
        try:
            deployment = get_deployment(resource_group_name=resource_group,
                                        deployment_name=deploy_name)
            logger.info(f'Deployment {deploy_name!r} already exists. '
                        'Skipping deployment creation.')

            outputs = deployment.properties.outputs
            if outputs is not None:
                deployment_exists = True
                break
        except azure.exceptions().ResourceNotFoundError:
            deployment_exists = False

    if not deployment_exists:
        logger.info(f'Creating/Updating deployment: {deployment_name}')
        create_or_update = get_azure_sdk_function(
            client=resource_client.deployments,
            function_name='create_or_update')
        # TODO (skypilot): this takes a long time (> 40 seconds) to run.
        outputs = create_or_update(
            resource_group_name=resource_group,
            deployment_name=deployment_name,
            parameters=parameters,
        ).result().properties.outputs

    # append output resource ids to be used with vm creation
    if custom_vnet is not None:
        subnet_id, nsg_id = custom_vnet
        logger.info(f'Using custom VNet: {vpc_name}')
        provider_config['subnet'] = subnet_id
        if nsg_id is not None:
            provider_config['nsg'] = nsg_id
        else:
            provider_config['nsg'] = outputs['nsg']['value']
    else:
        provider_config['nsg'] = outputs['nsg']['value']
        provider_config['subnet'] = outputs['subnet']['value']

    if custom_msi_id is not None:
        logger.info(f'Using custom managed identity: {custom_msi_id}')
        provider_config['msi'] = custom_msi_id
    else:
        provider_config['msi'] = outputs['msi']['value']

    return config


def _resolve_custom_managed_identity(remote_identity: Optional[str],
                                     subscription_id: str,
                                     resource_group: str) -> Optional[str]:
    """Resolve a custom managed identity name to a full resource ID.

    Returns None if remote_identity is not a custom identity (i.e., it is
    None or an enum value like LOCAL_CREDENTIALS / SERVICE_ACCOUNT).
    """
    if not isinstance(remote_identity, str) or not remote_identity:
        return None

    enum_values = {opt.value for opt in schemas.RemoteIdentityOptions}
    if remote_identity in enum_values:
        return None

    if remote_identity.startswith('/'):
        return remote_identity

    return (f'/subscriptions/{subscription_id}'
            f'/resourceGroups/{resource_group}'
            f'/providers/Microsoft.ManagedIdentity'
            f'/userAssignedIdentities/{remote_identity}')


def _remove_msi_resources_from_template(template: dict) -> None:
    """Remove MSI and role assignment resources from an ARM template.

    When using a custom managed identity, we don't need the ARM template
    to create a new MSI or assign roles. Removing these resources avoids
    requiring Microsoft.Authorization/roleAssignments/write permissions.
    """
    msi_types = {
        'Microsoft.ManagedIdentity/userAssignedIdentities',
        'Microsoft.Authorization/roleAssignments',
    }
    template['resources'] = [
        r for r in template['resources'] if r.get('type') not in msi_types
    ]
    # Remove the MSI output since it references the removed resource.
    template.get('outputs', {}).pop('msi', None)


def _resolve_custom_vnet(vpc_name: Optional[str], subscription_id: str,
                         resource_group: str,
                         location: str) -> Optional[Tuple[str, Optional[str]]]:
    """Resolve a custom VNet name to subnet and NSG resource IDs.

    Looks up the VNet by name in the resource group, finds the first
    subnet, and returns its NSG if one is associated.

    Returns:
        A (subnet_id, nsg_id) tuple, or None if vpc_name is not specified.
        nsg_id may be None if the subnet has no associated NSG.

    Raises:
        exceptions.ResourcesUnavailableError: If the VNet or a subnet
            cannot be found.
    """
    if vpc_name is None:
        return None

    network_client = azure.get_client('network', subscription_id)

    # Look up the VNet by name in the resource group.
    try:
        vnet = network_client.virtual_networks.get(resource_group, vpc_name)
    except azure.exceptions().ResourceNotFoundError as e:
        # Also try listing all VNets and matching by name, in case the
        # VNet is in a different resource group within the subscription.
        matches = []
        for v in network_client.virtual_networks.list_all():
            if v.name == vpc_name and v.location == location:
                matches.append(v)
                if len(matches) > 1:
                    break
        if not matches:
            raise exceptions.ResourcesUnavailableError(
                f'No VNet with name {vpc_name!r} found in resource group '
                f'{resource_group!r} or in subscription for location '
                f'{location!r}. Please check the VNet name and ensure it '
                f'exists.') from e
        if len(matches) > 1:
            raise exceptions.ResourcesUnavailableError(
                f'Multiple VNets with name {vpc_name!r} found in location '
                f'{location!r}. Please specify a unique VNet name.') from e
        vnet = matches[0]

    if not vnet.subnets:
        raise exceptions.ResourcesUnavailableError(
            f'VNet {vpc_name!r} has no subnets. Please create at least '
            f'one subnet in the VNet.')

    # Use the first subnet.
    subnet = vnet.subnets[0]
    subnet_id = subnet.id
    nsg_id = subnet.network_security_group.id if (
        subnet.network_security_group) else None

    logger.info(f'Resolved VNet {vpc_name!r}: subnet={subnet.name!r}, '
                f'nsg={"(from subnet)" if nsg_id else "(will create)"}')

    return subnet_id, nsg_id


def _remove_network_resources_from_template(template: dict) -> None:
    """Remove VNet and Subnet resources from an ARM template.

    When using a custom VNet, we don't need the ARM template to create
    networking resources. The NSG resource is kept if the custom VNet's
    subnet does not have an associated NSG.
    """
    network_types = {
        'Microsoft.Network/virtualNetworks',
    }
    template['resources'] = [
        r for r in template['resources'] if r.get('type') not in network_types
    ]
    # Remove the subnet output since it references the removed VNet.
    template.get('outputs', {}).pop('subnet', None)

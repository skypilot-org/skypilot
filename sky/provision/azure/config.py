"""Azure configuration bootstrapping.

Creates the resource group and deploys the configuration template to Azure for
a cluster to be launched.
"""
import json
import logging
from pathlib import Path
import random
import time
from typing import Any, Callable

from sky.adaptors import azure
from sky.provision import common
from sky.provision import constants

logger = logging.getLogger(__name__)

_RESOURCE_GROUP_WAIT_FOR_DELETION_TIMEOUT = 480  # 8 minutes


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


@common.log_function_start_end
def bootstrap_instances(
        region: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """See sky/provision/__init__.py"""
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
                        f'Azure resource group {resource_group} of a recent '
                        f'terminated cluster {cluster_name_on_cloud} is being '
                        'deleted. It can only be provisioned after it is fully'
                        'deleted. Waiting...')
                time.sleep(1)
                retry += 1
                continue
            raise
    else:
        raise TimeoutError(
            f'Timed out waiting for resource group {resource_group} to be '
            'deleted.')

    # load the template file
    current_path = Path(__file__).parent
    template_path = current_path.joinpath('azure-config-template.json')
    with open(template_path, 'r', encoding='utf-8') as template_fp:
        template = json.load(template_fp)

    logger.info(f'Using cluster name: {cluster_name_on_cloud}')

    subnet_mask = provider_config.get('subnet_mask')
    if subnet_mask is None:
        # choose a random subnet, skipping most common value of 0
        random.seed(cluster_name_on_cloud)
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
                    # We use the cluster name as the unique ID for the cluster,
                    # as we have already appended the user hash to the cluster
                    # name.
                    'value': cluster_name_on_cloud
                },
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
                cluster_name_on_cloud=cluster_name_on_cloud
        ))
        deployment_list = [deployment_name]
    else:
        deployment_name = constants.DEPLOYMENT_NAME
        deployment_list = [constants.DEPLOYMENT_NAME,
                           constants.LEGACY_DEPLOYMENT_NAME]

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
    provider_config['msi'] = outputs['msi']['value']
    provider_config['nsg'] = outputs['nsg']['value']
    provider_config['subnet'] = outputs['subnet']['value']

    return config

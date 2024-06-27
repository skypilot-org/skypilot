from hashlib import sha256
import json
import logging
from pathlib import Path
import random
import time
from typing import Any, Callable

from azure.mgmt.resource.resources.models import DeploymentMode

from sky.adaptors import azure
from sky.provision import common

UNIQUE_ID_LEN = 4

logger = logging.getLogger(__name__)


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
def bootstrap_instances(region: str, cluster_name: str, config: common.ProvisionConfig) -> common.ProvisionConfig:
    """See sky/provision/__init__.py"""
    provider_config = config.provider_config
    subscription_id = provider_config.get('subscription_id')
    if subscription_id is None:
        subscription_id = azure.get_subscription_id()
    # Increase the timeout to fix the Azure get-access-token (used by ray azure
    # node_provider) timeout issue.
    # Tracked in https://github.com/Azure/azure-cli/issues/20404#issuecomment-1249575110
    resource_client = azure.get_client('resource', subscription_id)
    provider_config['subscription_id'] = subscription_id
    logger.info(f'Using subscription id: {subscription_id}')

    assert ('resource_group' in provider_config
           ), 'Provider config must include resource_group field'
    resource_group = provider_config['resource_group']

    assert (
        'location'
        in provider_config), 'Provider config must include location field'
    params = {'location': provider_config['location']}

    if 'tags' in provider_config:
        params['tags'] = provider_config['tags']

    logger.info(f'Creating/Updating resource group: {resource_group}')
    rg_create_or_update = get_azure_sdk_function(
        client=resource_client.resource_groups,
        function_name='create_or_update')
    rg_create_or_update(resource_group_name=resource_group, parameters=params)

    # load the template file
    current_path = Path(__file__).parent
    template_path = current_path.joinpath('azure-config-template.json')
    with open(template_path, 'r') as template_fp:
        template = json.load(template_fp)

    logger.info('Using cluster name: %s', config['cluster_name'])

    # set unique id for resources in this cluster
    unique_id = provider_config.get('unique_id')
    if unique_id is None:
        hasher = sha256()
        hasher.update(provider_config['resource_group'].encode('utf-8'))
        unique_id = hasher.hexdigest()[:UNIQUE_ID_LEN]
    else:
        unique_id = str(unique_id)
    provider_config['unique_id'] = unique_id
    logger.info('Using unique id: %s', unique_id)
    cluster_id = '{}-{}'.format(config['cluster_name'], unique_id)

    subnet_mask = provider_config.get('subnet_mask')
    if subnet_mask is None:
        # choose a random subnet, skipping most common value of 0
        random.seed(unique_id)
        subnet_mask = '10.{}.0.0/16'.format(random.randint(1, 254))
    logger.info('Using subnet mask: %s', subnet_mask)

    parameters = {
        'properties': {
            'mode': DeploymentMode.incremental,
            'template': template,
            'parameters': {
                'subnet': {
                    'value': subnet_mask
                },
                'clusterId': {
                    'value': cluster_id
                },
            },
        }
    }

    create_or_update = get_azure_sdk_function(
        client=resource_client.deployments, function_name='create_or_update')
    # TODO (skypilot): this takes a long time (> 40 seconds) for stopping an
    # azure VM, and this can be called twice during ray down.
    outputs = (create_or_update(
        resource_group_name=resource_group,
        deployment_name='ray-config',
        parameters=parameters,
    ).result().properties.outputs)

    # We should wait for the NSG to be created before opening any ports
    # to avoid overriding the newly-added NSG rules.
    nsg_id = outputs['nsg']['value']

    # append output resource ids to be used with vm creation
    provider_config['msi'] = outputs['msi']['value']
    provider_config['nsg'] = nsg_id
    provider_config['subnet'] = outputs['subnet']['value']

    return config

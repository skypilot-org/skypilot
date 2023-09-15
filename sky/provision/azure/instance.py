"""Azure instance provisioning."""
from typing import Any, Callable, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import azure

logger = sky_logging.init_logger(__name__)

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'


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
            '"{obj}" object has no {func} or begin_{func} attribute'.format(
                obj={client.__name__}, func=function_name))
    return func


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']
    network_client = azure.get_client('network', subscription_id)
    create_or_update = get_azure_sdk_function(
        client=network_client.security_rules, function_name='create_or_update')
    rule_name = f'user-ports-{"-".join(ports)}'

    def security_rule_parameters(priority: int) -> Dict[str, Any]:
        return {
            'priority': priority,
            'protocol': 'TCP',
            'access': 'Allow',
            'direction': 'Inbound',
            'sourceAddressPrefix': '*',
            'sourcePortRange': '*',
            'destinationAddressPrefix': '*',
            'destinationPortRanges': ports,
        }

    list_network_security_groups = get_azure_sdk_function(
        client=network_client.network_security_groups, function_name='list')
    for nsg in list_network_security_groups(resource_group):
        try:
            # Azure NSG rules have a priority field that determines the order
            # in which they are applied. The priority must be unique across
            # all inbound rules in one NSG.
            max_inbound_priority = max([
                rule.priority
                for rule in nsg.security_rules
                if rule.direction == 'Inbound'
            ])
            create_or_update(resource_group, nsg.name, rule_name,
                             security_rule_parameters(max_inbound_priority + 1))
        except azure.http_error_exception() as e:
            logger.warning(
                f'Failed to open ports {ports} in NSG {nsg.name}: {e}')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    # Azure will automatically cleanup network security groups when cleanup
    # resource group. So we don't need to do anything here.
    del cluster_name_on_cloud, provider_config  # Unused.

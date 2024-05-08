"""Azure instance provisioning."""
import logging
from typing import Any, Callable, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import azure
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Suppress noisy logs from Azure SDK. Reference:
# https://github.com/Azure/azure-sdk-for-python/issues/9422
azure_logger = logging.getLogger('azure')
azure_logger.setLevel(logging.WARNING)

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
    # The NSG should have been created by the cluster provisioning.
    update_network_security_groups = get_azure_sdk_function(
        client=network_client.network_security_groups,
        function_name='create_or_update')
    list_network_security_groups = get_azure_sdk_function(
        client=network_client.network_security_groups, function_name='list')
    for nsg in list_network_security_groups(resource_group):
        try:
            # Azure NSG rules have a priority field that determines the order
            # in which they are applied. The priority must be unique across
            # all inbound rules in one NSG.
            priority = max(rule.priority
                           for rule in nsg.security_rules
                           if rule.direction == 'Inbound') + 1
            nsg.security_rules.append(
                azure.create_security_rule(
                    name=f'sky-ports-{cluster_name_on_cloud}-{priority}',
                    priority=priority,
                    protocol='Tcp',
                    access='Allow',
                    direction='Inbound',
                    source_address_prefix='*',
                    source_port_range='*',
                    destination_address_prefix='*',
                    destination_port_ranges=ports,
                ))
            poller = update_network_security_groups(resource_group, nsg.name,
                                                    nsg)
            poller.wait()
            if poller.status() != 'Succeeded':
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(f'Failed to open ports {ports} in NSG '
                                     f'{nsg.name}: {poller.status()}')
        except azure.exceptions().HttpResponseError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to open ports {ports} in NSG {nsg.name}.') from e


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    # Azure will automatically cleanup network security groups when cleanup
    # resource group. So we don't need to do anything here.
    del cluster_name_on_cloud, ports, provider_config  # Unused.

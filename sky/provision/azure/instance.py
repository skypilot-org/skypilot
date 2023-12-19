"""Azure instance provisioning."""
import time
from typing import Any, Callable, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import azure
from sky.utils import common_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'
_OPEN_PORTS_MAX_RETRY = 6


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
        backoff = common_utils.Backoff()
        open_ports_success = False

        for _ in range(_OPEN_PORTS_MAX_RETRY):
            success = True
            try:
                # Azure NSG rules have a priority field that determines
                # the order in which they are applied. The priority must
                # be unique across all inbound rules in one NSG.
                priority = max(rule.priority
                               for rule in nsg.security_rules
                               if rule.direction == 'Inbound') + 1
                sr_name = f'sky-ports-{cluster_name_on_cloud}-{priority}'
                nsg.security_rules.append(
                    azure.create_security_rule(
                        name=sr_name,
                        priority=priority,
                        protocol='Tcp',
                        access='Allow',
                        direction='Inbound',
                        source_address_prefix='*',
                        source_port_range='*',
                        destination_address_prefix='*',
                        destination_port_ranges=ports,
                    ))
                poller = update_network_security_groups(resource_group,
                                                        nsg.name, nsg)
                poller.wait()
                if poller.status() != 'Succeeded':
                    logger.info(f'Failed to open ports {ports} in NSG '
                                f'{nsg.name}. Poller status: {poller.status()}')
                    success = False
                new_nsg = poller.result()
                new_rule_found = False
                for rule in new_nsg.security_rules:
                    if rule.name == sr_name:
                        new_rule_found = True
                        break
                if not new_rule_found:
                    logger.info(f'Failed to open ports {ports} in NSG '
                                f'{nsg.name}. Rule {sr_name} not found.')
                    success = False
            except Exception as e:  # pylint: disable=broad-except
                logger.info(f'Failed to open ports {ports} in NSG {nsg.name}. '
                            f'Error: {e}')
                success = False
            if success:
                open_ports_success = True
                break
            else:
                interval = backoff.current_backoff()
                logger.info(f'Retrying in {interval} seconds.')
                time.sleep(interval)

        if not open_ports_success:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to open ports {ports} in NSG {nsg.name}.')


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    # Azure will automatically cleanup network security groups when cleanup
    # resource group. So we don't need to do anything here.
    del cluster_name_on_cloud, provider_config  # Unused.

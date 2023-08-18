"""Azure instance provisioning."""
from typing import Any, Callable, Dict, Optional, Set

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


def _extract_metadata(vm, provider_config):
    # get tags
    metadata = {'name': vm.name, 'tags': vm.tags, 'status': ''}

    # get status
    resource_group = provider_config['resource_group']
    subscription_id = provider_config['subscription_id']

    compute_client = azure.get_client('compute', subscription_id)
    network_client = azure.get_client('network', subscription_id)

    instance = compute_client.virtual_machines.instance_view(
        resource_group_name=resource_group, vm_name=vm.name).as_dict()

    for status in instance['statuses']:
        code, state = status['code'].split('/')
        # skip provisioning status
        if code == 'PowerState':
            metadata['status'] = state
            break

    # get ip data
    nic_id = vm.network_profile.network_interfaces[0].id
    metadata['nic_name'] = nic_id.split('/')[-1]
    nic = network_client.network_interfaces.get(
        resource_group_name=resource_group,
        network_interface_name=metadata['nic_name'],
    )
    ip_config = nic.ip_configurations[0]

    if not provider_config.get('use_internal_ips', False):
        public_ip_id = ip_config.public_ip_address.id
        metadata['public_ip_name'] = public_ip_id.split('/')[-1]
        public_ip = network_client.public_ip_addresses.get(
            resource_group_name=resource_group,
            public_ip_address_name=metadata['public_ip_name'],
        )
        metadata['external_ip'] = public_ip.ip_address

    metadata['internal_ip'] = ip_config.private_ip_address

    return metadata


def _filter_instances(
    cluster_name: str,
    provider_config,
    tag_filters,
    worker_only: bool,
):
    subscription_id = provider_config['subscription_id']
    # add cluster name filter to only get nodes from this cluster
    cluster_tag_filters = {**tag_filters, TAG_RAY_CLUSTER_NAME: cluster_name}
    if worker_only:
        cluster_tag_filters[TAG_RAY_NODE_KIND] = 'worker'

    def match_tags(vm):
        for k, v in cluster_tag_filters.items():
            if vm.tags.get(k) != v:
                return False
        return True

    compute_client = azure.get_client('compute', subscription_id)
    vms = compute_client.virtual_machines.list(
        resource_group_name=provider_config['resource_group'])

    nodes = [
        _extract_metadata(vm, provider_config)
        for vm in filter(match_tags, vms)
    ]
    return {node['name']: node for node in nodes}


def stop_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    assert provider_config is not None, cluster_name
    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']

    instances_to_stop = _filter_instances(
        cluster_name,
        provider_config,
        {},
        worker_only,
    )
    compute_client = azure.get_client('compute', subscription_id)

    stop = get_azure_sdk_function(
        client=compute_client.virtual_machines,
        function_name='deallocate',
    )
    for node_id in instances_to_stop.keys():
        stop(resource_group_name=resource_group, vm_name=node_id)


def terminate_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name
    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']
    compute_client = azure.get_client('compute', subscription_id)
    network_client = azure.get_client('network', subscription_id)

    if not worker_only:
        # If we are going to terminate all instances, then
        # we just take down the resource group in case of resource leak.
        resource_client = azure.get_client('resource', subscription_id)
        resource_client.resource_groups.begin_delete(resource_group)
        return

    instances_to_terminate = _filter_instances(
        cluster_name,
        provider_config,
        {},
        worker_only,
    )

    disks: Set[str] = set()

    for node_id in instances_to_terminate:
        vm = compute_client.virtual_machines.get(
            resource_group_name=resource_group, vm_name=node_id)
        disks.update(d.name for d in vm.storage_profile.data_disks)
        disks.add(vm.storage_profile.os_disk.name)

    # delete machine, must wait for this to complete
    delete = get_azure_sdk_function(client=compute_client.virtual_machines,
                                    function_name='delete')

    operations = []
    for node_id in instances_to_terminate:
        try:
            operations.append(
                delete(resource_group_name=resource_group, vm_name=node_id))
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to delete VM: {}'.format(e))
    for opr in operations:
        try:
            opr.wait()
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to delete VM: {}'.format(e))

    for metadata in instances_to_terminate.values():
        # delete nic
        try:
            delete = get_azure_sdk_function(
                client=network_client.network_interfaces,
                function_name='delete',
            )
            delete(
                resource_group_name=resource_group,
                network_interface_name=metadata['nic_name'],
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to delete nic: {}'.format(e))

        # delete ip address
        if 'public_ip_name' in metadata:
            try:
                delete = get_azure_sdk_function(
                    client=network_client.public_ip_addresses,
                    function_name='delete',
                )
                delete(
                    resource_group_name=resource_group,
                    public_ip_address_name=metadata['public_ip_name'],
                )
            except Exception as e:  # pylint: disable=broad-except
                logger.warning('Failed to delete public ip: {}'.format(e))

    # delete disks
    delete = get_azure_sdk_function(client=compute_client.disks,
                                    function_name='delete')
    for disk in disks:
        try:
            delete(resource_group_name=resource_group, disk_name=disk)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to delete disk: {}'.format(e))

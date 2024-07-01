"""Azure instance provisioning."""
import logging
from multiprocessing import pool
import typing
from typing import Any, Callable, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import azure
from sky.utils import common_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from azure.mgmt import compute as azure_compute

logger = sky_logging.init_logger(__name__)

# Suppress noisy logs from Azure SDK. Reference:
# https://github.com/Azure/azure-sdk-for-python/issues/9422
azure_logger = logging.getLogger('azure')
azure_logger.setLevel(logging.WARNING)

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'

_RESOURCE_GROUP_NOT_FOUND_ERROR_MESSAGE = 'ResourceGroupNotFound'


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


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)

    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']
    compute_client = azure.get_client('compute', subscription_id)
    tag_filters = {TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        tag_filters[TAG_RAY_NODE_KIND] = 'worker'

    nodes = _filter_instances(compute_client, tag_filters, resource_group)
    stop_virtual_machine = get_azure_sdk_function(
        client=compute_client.virtual_machines, function_name='deallocate')
    with pool.ThreadPool() as p:
        p.starmap(stop_virtual_machine,
                  [(resource_group, node.name) for node in nodes])


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    # TODO(zhwu): check the following. Also, seems we can directly force
    # delete a resource group.
    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']
    if worker_only:
        compute_client = azure.get_client('compute', subscription_id)
        delete_virtual_machine = get_azure_sdk_function(
            client=compute_client.virtual_machines, function_name='delete')
        filters = {
            TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
            TAG_RAY_NODE_KIND: 'worker'
        }
        nodes = _filter_instances(compute_client, filters, resource_group)
        with pool.ThreadPool() as p:
            p.starmap(delete_virtual_machine,
                      [(resource_group, node.name) for node in nodes])
        return

    assert provider_config is not None, cluster_name_on_cloud

    resource_group_client = azure.get_client('resource', subscription_id)
    delete_resource_group = get_azure_sdk_function(
        client=resource_group_client.resource_groups, function_name='delete')

    delete_resource_group(resource_group, force_deletion_types=None)


def _get_vm_status(compute_client: 'azure_compute.ComputeManagementClient',
                   vm_name: str, resource_group: str) -> str:
    instance = compute_client.virtual_machines.instance_view(
        resource_group_name=resource_group, vm_name=vm_name).as_dict()
    for status in instance['statuses']:
        code_state = status['code'].split('/')
        # It is possible that sometimes the 'code' is empty string, and we
        # should skip them.
        if len(code_state) != 2:
            continue
        code, state = code_state
        # skip provisioning status
        if code == 'PowerState':
            return state
    raise ValueError(f'Failed to get power state for VM {vm_name}: {instance}')


def _filter_instances(
        compute_client: 'azure_compute.ComputeManagementClient',
        filters: Dict[str, str],
        resource_group: str) -> List['azure_compute.models.VirtualMachine']:

    def match_tags(vm):
        for k, v in filters.items():
            if vm.tags.get(k) != v:
                return False
        return True

    try:
        list_virtual_machines = get_azure_sdk_function(
            client=compute_client.virtual_machines, function_name='list')
        vms = list_virtual_machines(resource_group_name=resource_group)
        nodes = list(filter(match_tags, vms))
    except azure.exceptions().ResourceNotFoundError as e:
        if _RESOURCE_GROUP_NOT_FOUND_ERROR_MESSAGE in str(e):
            return []
        raise
    return nodes


@common_utils.retry
def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    status_map = {
        'starting': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        # 'stopped' in Azure means Stopped (Allocated), which still bills
        # for the VM.
        'stopping': status_lib.ClusterStatus.INIT,
        'stopped': status_lib.ClusterStatus.INIT,
        # 'VM deallocated' in Azure means Stopped (Deallocated), which does not
        # bill for the VM.
        'deallocating': status_lib.ClusterStatus.STOPPED,
        'deallocated': status_lib.ClusterStatus.STOPPED,
    }
    provisioning_state_map = {
        'Creating': status_lib.ClusterStatus.INIT,
        'Updating': status_lib.ClusterStatus.INIT,
        'Failed': status_lib.ClusterStatus.INIT,
        'Migrating': status_lib.ClusterStatus.INIT,
        'Deleting': None,
        # Succeeded in provisioning state means the VM is provisioned but not
        # necessarily running. We exclude Succeeded state here, and the caller
        # should determine the status of the VM based on the power state.
        # 'Succeeded': status_lib.ClusterStatus.UP,
    }

    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']
    compute_client = azure.get_client('compute', subscription_id)
    filters = {TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    nodes = _filter_instances(compute_client, filters, resource_group)
    statuses = {}

    def _fetch_and_map_status(
            compute_client: 'azure_compute.ComputeManagementClient',
            node: 'azure_compute.models.VirtualMachine',
            resource_group: str) -> None:
        if node.provisioning_state in provisioning_state_map:
            status = provisioning_state_map[node.provisioning_state]
        else:
            original_status = _get_vm_status(compute_client, node.name,
                                             resource_group)
            if original_status not in status_map:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ClusterStatusFetchingError(
                        f'Failed to parse status from Azure response: {status}')
            status = status_map[original_status]
        if status is None and non_terminated_only:
            return
        statuses[node.name] = status

    with pool.ThreadPool() as p:
        p.starmap(_fetch_and_map_status,
                  [(compute_client, node, resource_group) for node in nodes])

    return statuses

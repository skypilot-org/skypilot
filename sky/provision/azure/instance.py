"""Azure instance provisioning."""
import copy
import logging
import enum
from multiprocessing import pool
import time
from typing import Any, Callable, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import azure
from sky.utils import common_utils
from sky.utils import ux_utils
from sky.provision import common
from sky.provision import constants

logger = sky_logging.init_logger(__name__)

# Suppress noisy logs from Azure SDK. Reference:
# https://github.com/Azure/azure-sdk-for-python/issues/9422
azure_logger = logging.getLogger('azure')
azure_logger.setLevel(logging.WARNING)

_RESUME_INSTANCE_TIMEOUT = 480  # 8 minutes
_RESUME_PER_INSTANCE_TIMEOUT = 120  # 2 minutes


class AzureInstanceStatus(enum.Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    STOPPING = 'stopping'
    STOPPED = 'stopped'
    DELETING = 'deleting'

    POWER_STATE_MAP = {
        'starting': PENDING,
        'running': RUNNING,
        # 'stopped' in Azure means Stopped (Allocated), which still bills
        # for the VM.
        'stopping': STOPPING,
        'stopped': STOPPED,
        # 'VM deallocated' in Azure means Stopped (Deallocated), which does not
        # bill for the VM.
        'deallocating': STOPPING,
        'deallocated': STOPPED,
    }
    PROVISIONING_STATE_MAP = {
        'Creating': PENDING,
        'Updating': PENDING,
        'Failed': PENDING,
        'Migrating': PENDING,
        'Deleting': DELETING,
        # Succeeded in provisioning state means the VM is provisioned but not
        # necessarily running.
        # 'Succeeded': status_lib.ClusterStatus.UP,
    }

    CLUSTER_STATUS_MAP = {
        PENDING: status_lib.ClusterStatus.INIT,
        STOPPING: status_lib.ClusterStatus.INIT,
        RUNNING: status_lib.ClusterStatus.UP,
        STOPPED: status_lib.ClusterStatus.STOPPED,
        DELETING: None,
    }

    @classmethod
    def from_raw_states(cls, provisioning_state: str, power_state: str) -> 'AzureInstanceStatus':
        if provisioning_state in cls.PROVISIONING_STATE_MAP:
            status = cls.PROVISIONING_STATE_MAP[provisioning_state]
        else:
            if power_state not in cls.POWER_STATE_MAP:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ClusterStatusFetchingError(
                        f'Failed to parse status from Azure response: {status}')
            status = cls.POWER_STATE_MAP[power_state]
        return status
        

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        return self.CLUSTER_STATUS_MAP.get(self)



_WAIT_NSG_CREATION_NUM_TIMEOUT_SECONDS = 600


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

    
def _get_head_instance_id(instances: List) -> Optional[str]:
    head_instance_id = None
    head_node_markers = (
        (constants.TAG_SKYPILOT_HEAD_NODE, '1'),
        (constants.TAG_RAY_NODE_KIND, 'head'),  # backward compat with Ray
    )
    for inst in instances:
        for k, v in inst.tags.items():
            if (k, v) in head_node_markers:
                if head_instance_id is not None:
                    logger.warning(
                        'There are multiple head nodes in the cluster '
                        f'(current head instance id: {head_instance_id}, '
                        f'newly discovered id: {inst.id}). It is likely '
                        f'that something goes wrong.')
                head_instance_id = inst.id
                break
    return head_instance_id

def _create_instances(compute_client, cluster_name_on_cloud: str, resource_group: str, node_config: Dict[str, Any], tags: Dict[str, str], count: int) -> List:
    # load the template file
    current_path = pathlib.Path(__file__).parent
    template_path = current_path.joinpath("azure-vm-template.json")
    with open(template_path, "r") as template_fp:
        template = json.load(template_fp)

def run_instances(region: str, cluster_name_on_cloud: str, config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    provider_config = config.provider_config
    resource_group = provider_config['resource_group']
    subscription_id = provider_config['subscription_id']
    compute_client = azure.get_client('compute', subscription_id)

    resumed_instance_ids: List[str] = []
    created_instance_ids: List[str] = []

    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(config.tags).items()))
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}

    existing_instances = _filter_instances(filters=filters, resource_group=resource_group)
    existing_instances.sort(key=lambda x: x.name)
    non_deleting_existing_instances = []


    pending_instances = []
    running_instances = []
    stopping_instances = []
    stopped_instances = []

    for instance in existing_instances:
        status = _get_instance_status(compute_client, instance.name, resource_group)

        if status == AzureInstanceStatus.RUNNING:
            running_instances.append(instance)
        elif status == AzureInstanceStatus.STOPPED:
            stopped_instances.append(instance)
        elif status == AzureInstanceStatus.STOPPING:
            stopping_instances.append(instance)
        elif status == AzureInstanceStatus.PENDING:
            pending_instances.append(instance)
        
        if status != AzureInstanceStatus.DELETING:
            non_deleting_existing_instances.append(instance)
    

    def _create_instance_tag(target_instance, is_head: bool = True) -> str:
        if is_head:
            new_instance_tags = [{
                'Key': constants.TAG_SKYPILOT_HEAD_NODE,
                'Value': '1'
            }, {
                'Key': constants.TAG_RAY_NODE_KIND,
                'Value': 'head'
            }, {
                'Key': 'Name',
                'Value': f'sky-{cluster_name_on_cloud}-head'
            }]
        else:
            new_instance_tags = [{
                'Key': constants.TAG_SKYPILOT_HEAD_NODE,
                'Value': '0'
            }, {
                'Key': constants.TAG_RAY_NODE_KIND,
                'Value': 'worker'
            }, {
                'Key': 'Name',
                'Value': f'sky-{cluster_name_on_cloud}-worker'
            }]
        tags = target_instance.tags
        tags.update(new_instance_tags)

        update = get_azure_sdk_function(
            compute_client.virtual_machines, 'update')
        update(resource_group, target_instance.name, parameters={'tags': tags})
        return target_instance.name

    head_instance_id = _get_head_instance_id(non_deleting_existing_instances)
    if head_instance_id is None:
        if running_instances:
            head_instance_id = _create_instance_tag(running_instances[0])
        elif pending_instances:
            head_instance_id = _create_instance_tag(pending_instances[0])
    
    if config.resume_stopped_nodes and len(existing_instances)> config.count:
        raise RuntimeError(
            'The number of running/stopped/stopping '
            f'instances combined ({len(non_deleting_existing_instances)}) in '
            f'cluster "{cluster_name_on_cloud}" is greater than the '
            f'number requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')
    
    to_start_count = config.count - len(non_deleting_existing_instances) - len(pending_instances)

    if to_start_count < 0:
        raise RuntimeError(
            'The number of running+pending instances '
            f'({config.count - to_start_count}) in cluster '
            f'"{cluster_name_on_cloud}" is greater than the number '
            f'requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')
    
    if config.resume_stopped_nodes and to_start_count > 0 and (
        stopping_instances or stopped_instances):
        time_start = time.time()
        if stopping_instances:
            plural = 's' if len(stopping_instances) > 1 else ''
            verb = 'are' if len(stopping_instances) > 1 else 'is'
            # TODO(zhwu): double check the correctness of the following on Azure
            logger.warning(
                f'Instance{plural} {stopping_instances} {verb} still in '
                'STOPPING state on Azure. It can only be resumed after it is '
                'fully STOPPED. Waiting ...')
        while (stopping_instances and to_start_count > len(stopped_instances)  and
               time.time() - time_start < _RESUME_INSTANCE_TIMEOUT):
            inst = stopping_instances.pop(0)
            per_instance_time_start = time.time()
            while (time.time() - per_instance_time_start <
                _RESUME_PER_INSTANCE_TIMEOUT):
                status = _get_instance_status(compute_client, instance.name, resource_group)
                if status in (AzureInstanceStatus.RUNNING, AzureInstanceStatus.STOPPED):
                    break
                time.sleep(1)
            else:
                logger.warning(
                        f'Instance {inst.id} is still in stopping state '
                        f'(Timeout: {_RESUME_PER_INSTANCE_TIMEOUT}). '
                        'Retrying ...')
                stopping_instances.append(inst)
                time.sleep(5)
        if stopping_instances and to_start_count > len(stopped_instances):
            msg = ('Timeout for waiting for existing instances '
                   f'{stopping_instances} in STOPPING state to '
                   'be STOPPED before restarting them. Please try again later.')
            logger.error(msg)
            raise RuntimeError(msg)

        resumed_instances = stopped_instances[:to_start_count]
        resumed_instances.sort(key=lambda x: x.name)
        resumed_instance_ids = [t.name for t in resumed_instances]
        logger.debug(f'Resuming stopped instances {resumed_instance_ids}.')
        start_virtual_machine = get_azure_sdk_function(
            compute_client.virtual_machines, 'start')
        with pool.ThreadPool() as p:
            p.starmap(start_virtual_machine,
                      [(resource_group, inst.name) for inst in resumed_instances])

        instances_to_tag = copy.copy(resumed_instances)
        if head_instance_id is None:
            head_instance_id = _create_instance_tag(instances_to_tag[0])
            instances_to_tag = instances_to_tag[1:]
        
        if tags:
            # empty tags will result in error in the API call
            with pool.ThreadPool() as p:
                p.starmap(
                    _create_instance_tag,
                    [(inst, False) for inst in instances_to_tag])
                
        to_start_count -= len(resumed_instances)

        if to_start_count > 0:
            created_instances = _create_instances()

        
    pass

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

    update_network_security_groups = get_azure_sdk_function(
        client=network_client.network_security_groups,
        function_name='create_or_update')
    list_network_security_groups = get_azure_sdk_function(
        client=network_client.network_security_groups, function_name='list')
    for nsg in list_network_security_groups(resource_group):
        try:
            # Wait the NSG creation to be finished before opening a port. The cluster
            # provisioning triggers the NSG creation, but it may not be finished yet.
            backoff = common_utils.Backoff(max_backoff_factor=1)
            start_time = time.time()
            while True:
                if nsg.provisioning_state not in ['Creating', 'Updating']:
                    break
                if time.time(
                ) - start_time > _WAIT_NSG_CREATION_NUM_TIMEOUT_SECONDS:
                    logger.warning(
                        f'Fails to wait for the creation of NSG {nsg.name} in '
                        f'{resource_group} within '
                        f'{_WAIT_NSG_CREATION_NUM_TIMEOUT_SECONDS} seconds. '
                        'Skip this NSG.')
                backoff_time = backoff.current_backoff()
                logger.info(f'NSG {nsg.name} is not created yet. Waiting for '
                            f'{backoff_time} seconds before checking again.')
                time.sleep(backoff_time)

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


# def _get_vm_ips(network_client, vm, resource_group: str,
#                 use_internal_ips: bool) -> Tuple[str, str]:
#     nic_id = vm.network_profile.network_interfaces[0].id
#     nic_name = nic_id.split("/")[-1]
#     nic = network_client.network_interfaces.get(
#         resource_group_name=resource_group,
#         network_interface_name=nic_name,
#     )
#     ip_config = nic.ip_configurations[0]

#     external_ip = None
#     if not use_internal_ips:
#         public_ip_id = ip_config.public_ip_address.id
#         public_ip_name = public_ip_id.split("/")[-1]
#         public_ip = network_client.public_ip_addresses.get(
#             resource_group_name=resource_group,
#             public_ip_address_name=public_ip_name,
#         )
#         external_ip = public_ip.ip_address

#     internal_ip = ip_config.private_ip_address

#     return (external_ip, internal_ip)


def _get_instance_status(compute_client, vm_name: str, resource_group: str) -> Optional[AzureInstanceStatus]:
    try:
        instance = compute_client.virtual_machines.instance_view(
        resource_group_name=resource_group, vm_name=vm_name)
    except azure.exceptions().ResourceNotFoundError as e:
        if 'ResourceNotFound' in str(e):
            return None
        raise
    provisioning_state = instance.provisioning_state
    instance_dict = instance.as_dict()
    for status in instance_dict['statuses']:
        code_state = status['code'].split('/')
        # It is possible that sometimes the 'code' is empty string, and we
        # should skip them.
        if len(code_state) != 2:
            continue
        code, state = code_state
        # skip provisioning status
        if code == 'PowerState':
            return AzureInstanceStatus.from_raw_states(provisioning_state, state)
    raise ValueError(f'Failed to parse status from Azure response: {instance_dict}')


def _filter_instances(compute_client, filters: Dict[str, str],
                      resource_group: str) -> List[Any]:

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
        if 'ResourceGroupNotFound' in str(e):
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

    subscription_id = provider_config['subscription_id']
    resource_group = provider_config['resource_group']
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    compute_client = azure.get_client('compute', subscription_id)
    nodes = _filter_instances(compute_client, filters, resource_group)
    statuses = {}

    def _fetch_and_map_status(node, resource_group: str) -> 'status_lib.ClusterStatus':
        compute_client = azure.get_client('compute', subscription_id)
        status = _get_instance_status(compute_client, node.name, resource_group)
        
        if status is None and non_terminated_only:
            return
        statuses[node.name] = status

    with pool.ThreadPool() as p:
        p.starmap(_fetch_and_map_status,
                  [(node, resource_group) for node in nodes])

    return statuses

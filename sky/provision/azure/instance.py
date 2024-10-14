"""Azure instance provisioning."""
import base64
import copy
import enum
import json
import logging
from multiprocessing import pool
import pathlib
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import azure
from sky.provision import common
from sky.provision import constants
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from azure.mgmt import compute as azure_compute
    from azure.mgmt import resource as azure_resource

logger = sky_logging.init_logger(__name__)

# Suppress noisy logs from Azure SDK. Reference:
# https://github.com/Azure/azure-sdk-for-python/issues/9422
azure_logger = logging.getLogger('azure')
azure_logger.setLevel(logging.WARNING)

_RESUME_INSTANCE_TIMEOUT = 480  # 8 minutes
_RESUME_PER_INSTANCE_TIMEOUT = 120  # 2 minutes
UNIQUE_ID_LEN = 4
_TAG_SKYPILOT_VM_ID = 'skypilot-vm-id'
_WAIT_CREATION_TIMEOUT_SECONDS = 600

_RESOURCE_GROUP_NOT_FOUND_ERROR_MESSAGE = 'ResourceGroupNotFound'
_POLL_INTERVAL = 1


class AzureInstanceStatus(enum.Enum):
    """Statuses enum for Azure instances with power and provisioning states."""
    PENDING = 'pending'
    RUNNING = 'running'
    STOPPING = 'stopping'
    STOPPED = 'stopped'
    DELETING = 'deleting'

    @classmethod
    def power_state_map(cls) -> Dict[str, 'AzureInstanceStatus']:
        return {
            'starting': cls.PENDING,
            'running': cls.RUNNING,
            # 'stopped' in Azure means Stopped (Allocated), which still bills
            # for the VM.
            'stopping': cls.STOPPING,
            'stopped': cls.STOPPED,
            # 'VM deallocated' in Azure means Stopped (Deallocated), which does
            # not bill for the VM.
            'deallocating': cls.STOPPING,
            'deallocated': cls.STOPPED,
        }

    @classmethod
    def provisioning_state_map(cls) -> Dict[str, 'AzureInstanceStatus']:
        return {
            'Creating': cls.PENDING,
            'Updating': cls.PENDING,
            'Failed': cls.PENDING,
            'Migrating': cls.PENDING,
            'Deleting': cls.DELETING,
            # Succeeded in provisioning state means the VM is provisioned but
            # not necessarily running. The caller should further check the
            # power state to determine the actual VM status.
            'Succeeded': cls.RUNNING,
        }

    @classmethod
    def cluster_status_map(
            cls
    ) -> Dict['AzureInstanceStatus', Optional[status_lib.ClusterStatus]]:
        return {
            cls.PENDING: status_lib.ClusterStatus.INIT,
            cls.STOPPING: status_lib.ClusterStatus.INIT,
            cls.RUNNING: status_lib.ClusterStatus.UP,
            cls.STOPPED: status_lib.ClusterStatus.STOPPED,
            cls.DELETING: None,
        }

    @classmethod
    def from_raw_states(cls, provisioning_state: str,
                        power_state: Optional[str]) -> 'AzureInstanceStatus':
        provisioning_state_map = cls.provisioning_state_map()
        power_state_map = cls.power_state_map()
        status = None
        if power_state is None:
            if provisioning_state not in provisioning_state_map:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ClusterStatusFetchingError(
                        'Failed to parse status from Azure response: '
                        f'{provisioning_state}')
            status = provisioning_state_map[provisioning_state]
        if status is None or status == cls.RUNNING:
            # We should further check the power state to determine the actual
            # VM status.
            if power_state not in power_state_map:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ClusterStatusFetchingError(
                        'Failed to parse status from Azure response: '
                        f'{power_state}.')
            status = power_state_map[power_state]
        if status is None:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    'Failed to parse status from Azure response: '
                    f'provisioning state ({provisioning_state}), '
                    f'power state ({power_state})')
        return status

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        return self.cluster_status_map().get(self)


def _get_azure_sdk_function(client: Any, function_name: str) -> Callable:
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


def _get_instance_ips(network_client, vm, resource_group: str,
                      use_internal_ips: bool) -> Tuple[str, Optional[str]]:
    nic_id = vm.network_profile.network_interfaces[0].id
    nic_name = nic_id.split('/')[-1]
    nic = network_client.network_interfaces.get(
        resource_group_name=resource_group,
        network_interface_name=nic_name,
    )
    ip_config = nic.ip_configurations[0]

    external_ip = None
    if not use_internal_ips:
        public_ip_id = ip_config.public_ip_address.id
        public_ip_name = public_ip_id.split('/')[-1]
        public_ip = network_client.public_ip_addresses.get(
            resource_group_name=resource_group,
            public_ip_address_name=public_ip_name,
        )
        external_ip = public_ip.ip_address

    internal_ip = ip_config.private_ip_address

    return (internal_ip, external_ip)


def _get_head_instance_id(instances: List) -> Optional[str]:
    head_instance_id = None
    head_node_tags = tuple(constants.HEAD_NODE_TAGS.items())
    for inst in instances:
        for k, v in inst.tags.items():
            if (k, v) in head_node_tags:
                if head_instance_id is not None:
                    logger.warning(
                        'There are multiple head nodes in the cluster '
                        f'(current head instance id: {head_instance_id}, '
                        f'newly discovered id: {inst.name}). It is likely '
                        f'that something goes wrong.')
                head_instance_id = inst.name
                break
    return head_instance_id


def _create_instances(
        compute_client: 'azure_compute.ComputeManagementClient',
        resource_client: 'azure_resource.ResourceManagementClient',
        cluster_name_on_cloud: str, resource_group: str,
        provider_config: Dict[str, Any], node_config: Dict[str, Any],
        tags: Dict[str, str], count: int) -> List:
    vm_id = uuid4().hex[:UNIQUE_ID_LEN]
    tags = {
        constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
        constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
        **constants.WORKER_NODE_TAGS,
        _TAG_SKYPILOT_VM_ID: vm_id,
        **tags,
    }
    node_tags = node_config['tags'].copy()
    node_tags.update(tags)

    # load the template file
    current_path = pathlib.Path(__file__).parent
    template_path = current_path.joinpath('azure-vm-template.json')
    with open(template_path, 'r', encoding='utf-8') as template_fp:
        template = json.load(template_fp)

    vm_name = f'{cluster_name_on_cloud}-{vm_id}'
    use_internal_ips = provider_config.get('use_internal_ips', False)

    template_params = node_config['azure_arm_parameters'].copy()
    # We don't include 'head' or 'worker' in the VM name as on Azure the VM
    # name is immutable and we may change the node type for existing VM in the
    # multi-node cluster, due to manual termination of the head node.
    template_params['vmName'] = vm_name
    template_params['provisionPublicIp'] = not use_internal_ips
    template_params['vmTags'] = node_tags
    template_params['vmCount'] = count
    template_params['msi'] = provider_config['msi']
    template_params['nsg'] = provider_config['nsg']
    template_params['subnet'] = provider_config['subnet']
    # In Azure, cloud-init script must be encoded in base64. For more
    # information, see:
    # https://learn.microsoft.com/en-us/azure/virtual-machines/custom-data
    template_params['cloudInitSetupCommands'] = (base64.b64encode(
        template_params['cloudInitSetupCommands'].encode('utf-8')).decode(
            'utf-8'))

    if node_config.get('need_nvidia_driver_extension', False):
        # pylint: disable=line-too-long
        # Configure driver extension for A10 GPUs. A10 GPUs requires a
        # special type of drivers which is available at Microsoft HPC
        # extension. Reference: https://forums.developer.nvidia.com/t/ubuntu-22-04-installation-driver-error-nvidia-a10/285195/2
        for r in template['resources']:
            if r['type'] == 'Microsoft.Compute/virtualMachines':
                # Add a nested extension resource for A10 GPUs
                r['resources'] = [
                    {
                        'type': 'extensions',
                        'apiVersion': '2015-06-15',
                        'location': '[variables(\'location\')]',
                        'dependsOn': [
                            '[concat(\'Microsoft.Compute/virtualMachines/\', parameters(\'vmName\'), copyIndex())]'
                        ],
                        'name': 'NvidiaGpuDriverLinux',
                        'properties': {
                            'publisher': 'Microsoft.HpcCompute',
                            'type': 'NvidiaGpuDriverLinux',
                            'typeHandlerVersion': '1.9',
                            'autoUpgradeMinorVersion': True,
                            'settings': {},
                        },
                    },
                ]
                break

    parameters = {
        'properties': {
            'mode': azure.deployment_mode().incremental,
            'template': template,
            'parameters': {
                key: {
                    'value': value
                } for key, value in template_params.items()
            },
        }
    }

    create_or_update = _get_azure_sdk_function(
        client=resource_client.deployments, function_name='create_or_update')
    create_or_update(
        resource_group_name=resource_group,
        deployment_name=vm_name,
        parameters=parameters,
    ).wait()

    performance_tier = node_config.get('disk_performance_tier', None)
    if performance_tier is not None:
        disks = compute_client.disks.list_by_resource_group(resource_group)
        for disk in disks:
            name = disk.name
            # TODO(tian): Investigate if we can use Python SDK to update this.
            subprocess_utils.run_no_outputs(
                f'az disk update -n {name} -g {resource_group} '
                f'--set tier={performance_tier}')

    filters = {
        constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
        _TAG_SKYPILOT_VM_ID: vm_id
    }
    instances = _filter_instances(compute_client, resource_group, filters)
    assert len(instances) == count, (len(instances), count)
    return instances


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    # TODO(zhwu): This function is too long. We should refactor it.
    provider_config = config.provider_config
    resource_group = provider_config['resource_group']
    subscription_id = provider_config['subscription_id']
    compute_client = azure.get_client('compute', subscription_id)

    instances_to_resume = []
    resumed_instance_ids: List[str] = []
    created_instance_ids: List[str] = []

    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(config.tags).items()))
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}

    non_deleting_states = (set(AzureInstanceStatus) -
                           {AzureInstanceStatus.DELETING})
    existing_instances = _filter_instances(
        compute_client,
        tag_filters=filters,
        resource_group=resource_group,
        status_filters=list(non_deleting_states),
    )
    logger.debug(
        f'run_instances: Found {[inst.name for inst in existing_instances]} '
        'existing instances in cluster.')
    existing_instances.sort(key=lambda x: x.name)

    pending_instances = []
    running_instances = []
    stopping_instances = []
    stopped_instances = []

    for instance in existing_instances:
        status = _get_instance_status(compute_client, instance, resource_group)
        logger.debug(
            f'run_instances: Instance {instance.name} has status {status}.')

        if status == AzureInstanceStatus.RUNNING:
            running_instances.append(instance)
        elif status == AzureInstanceStatus.STOPPED:
            stopped_instances.append(instance)
        elif status == AzureInstanceStatus.STOPPING:
            stopping_instances.append(instance)
        elif status == AzureInstanceStatus.PENDING:
            pending_instances.append(instance)

    def _create_instance_tag(target_instance, is_head: bool = True) -> str:
        new_instance_tags = (constants.HEAD_NODE_TAGS
                             if is_head else constants.WORKER_NODE_TAGS)

        tags = target_instance.tags
        tags.update(new_instance_tags)

        update = _get_azure_sdk_function(compute_client.virtual_machines,
                                         'update')
        update(resource_group, target_instance.name, parameters={'tags': tags})
        return target_instance.name

    head_instance_id = _get_head_instance_id(existing_instances)
    if head_instance_id is None:
        if running_instances:
            head_instance_id = _create_instance_tag(running_instances[0])
        elif pending_instances:
            head_instance_id = _create_instance_tag(pending_instances[0])

    if config.resume_stopped_nodes and len(existing_instances) > config.count:
        raise RuntimeError(
            'The number of pending/running/stopped/stopping '
            f'instances combined ({len(existing_instances)}) in '
            f'cluster "{cluster_name_on_cloud}" is greater than the '
            f'number requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    to_start_count = config.count - len(pending_instances) - len(
        running_instances)

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
                f'Instance{plural} {[inst.name for inst in stopping_instances]}'
                f' {verb} still in STOPPING state on Azure. It can only be '
                'resumed after it is fully STOPPED. Waiting ...')
        while (stopping_instances and
               to_start_count > len(stopped_instances) and
               time.time() - time_start < _RESUME_INSTANCE_TIMEOUT):
            inst = stopping_instances.pop(0)
            per_instance_time_start = time.time()
            while (time.time() - per_instance_time_start <
                   _RESUME_PER_INSTANCE_TIMEOUT):
                status = _get_instance_status(compute_client, inst,
                                              resource_group)
                if status == AzureInstanceStatus.STOPPED:
                    break
                time.sleep(1)
            else:
                logger.warning(
                    f'Instance {inst.name} is still in stopping state '
                    f'(Timeout: {_RESUME_PER_INSTANCE_TIMEOUT}). '
                    'Retrying ...')
                stopping_instances.append(inst)
                time.sleep(5)
                continue
            stopped_instances.append(inst)
        if stopping_instances and to_start_count > len(stopped_instances):
            msg = ('Timeout for waiting for existing instances '
                   f'{stopping_instances} in STOPPING state to '
                   'be STOPPED before restarting them. Please try again later.')
            logger.error(msg)
            raise RuntimeError(msg)

        instances_to_resume = stopped_instances[:to_start_count]
        instances_to_resume.sort(key=lambda x: x.name)
        instances_to_resume_ids = [t.name for t in instances_to_resume]
        logger.debug('run_instances: Resuming stopped instances '
                     f'{instances_to_resume_ids}.')
        start_virtual_machine = _get_azure_sdk_function(
            compute_client.virtual_machines, 'start')
        with pool.ThreadPool() as p:
            p.starmap(
                start_virtual_machine,
                [(resource_group, inst.name) for inst in instances_to_resume])
        resumed_instance_ids = instances_to_resume_ids

    to_start_count -= len(resumed_instance_ids)

    if to_start_count > 0:
        resource_client = azure.get_client('resource', subscription_id)
        logger.debug(f'run_instances: Creating {to_start_count} instances.')
        try:
            created_instances = _create_instances(
                compute_client=compute_client,
                resource_client=resource_client,
                cluster_name_on_cloud=cluster_name_on_cloud,
                resource_group=resource_group,
                provider_config=provider_config,
                node_config=config.node_config,
                tags=tags,
                count=to_start_count)
        except Exception as e:
            err_message = common_utils.format_exception(
                e, use_bracket=True).replace('\n', ' ')
            logger.error(f'Failed to create instances: {err_message}')
            raise
        created_instance_ids = [inst.name for inst in created_instances]

    non_running_instance_statuses = list(
        set(AzureInstanceStatus) - {AzureInstanceStatus.RUNNING})
    start = time.time()
    while True:
        # Wait for all instances to be in running state
        instances = _filter_instances(
            compute_client,
            resource_group,
            filters,
            status_filters=non_running_instance_statuses,
            included_instances=created_instance_ids + resumed_instance_ids)
        if not instances:
            break
        if time.time() - start > _WAIT_CREATION_TIMEOUT_SECONDS:
            raise TimeoutError(
                'run_instances: Timed out waiting for Azure instances to be '
                f'running: {instances}')
        logger.debug(f'run_instances: Waiting for {len(instances)} instances '
                     'in PENDING status.')
        time.sleep(_POLL_INTERVAL)

    running_instances = _filter_instances(
        compute_client,
        resource_group,
        filters,
        status_filters=[AzureInstanceStatus.RUNNING])
    head_instance_id = _get_head_instance_id(running_instances)
    instances_to_tag = copy.copy(running_instances)
    if head_instance_id is None:
        head_instance_id = _create_instance_tag(instances_to_tag[0])
        instances_to_tag = instances_to_tag[1:]
    else:
        instances_to_tag = [
            inst for inst in instances_to_tag if inst.name != head_instance_id
        ]

    if instances_to_tag:
        # Tag the instances in case the old resumed instances are not correctly
        # tagged.
        with pool.ThreadPool() as p:
            p.starmap(
                _create_instance_tag,
                # is_head=False for all wokers.
                [(inst, False) for inst in instances_to_tag])

    assert head_instance_id is not None, head_instance_id
    return common.ProvisionRecord(
        provider_name='azure',
        region=region,
        zone=None,
        cluster_name=cluster_name_on_cloud,
        head_instance_id=head_instance_id,
        created_instance_ids=created_instance_ids,
        resumed_instance_ids=resumed_instance_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py"""
    del region, cluster_name_on_cloud, state
    # We already wait for the instances to be running in run_instances.
    # So we don't need to wait here.


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """See sky/provision/__init__.py"""
    del region
    filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    resource_group = provider_config['resource_group']
    subscription_id = provider_config.get('subscription_id',
                                          azure.get_subscription_id())
    compute_client = azure.get_client('compute', subscription_id)
    network_client = azure.get_client('network', subscription_id)

    running_instances = _filter_instances(
        compute_client,
        resource_group,
        filters,
        status_filters=[AzureInstanceStatus.RUNNING])
    head_instance_id = _get_head_instance_id(running_instances)

    instances = {}
    use_internal_ips = provider_config.get('use_internal_ips', False)
    for inst in running_instances:
        internal_ip, external_ip = _get_instance_ips(network_client, inst,
                                                     resource_group,
                                                     use_internal_ips)
        instances[inst.name] = [
            common.InstanceInfo(
                instance_id=inst.name,
                internal_ip=internal_ip,
                external_ip=external_ip,
                tags=inst.tags,
            )
        ]
    instances = dict(sorted(instances.items(), key=lambda x: x[0]))
    return common.ClusterInfo(
        provider_name='azure',
        head_instance_id=head_instance_id,
        instances=instances,
        provider_config=provider_config,
    )


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
    tag_filters = {constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        tag_filters[constants.TAG_RAY_NODE_KIND] = 'worker'

    nodes = _filter_instances(compute_client, resource_group, tag_filters)
    stop_virtual_machine = _get_azure_sdk_function(
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
        delete_virtual_machine = _get_azure_sdk_function(
            client=compute_client.virtual_machines, function_name='delete')
        filters = {
            constants.TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud,
            constants.TAG_RAY_NODE_KIND: 'worker'
        }
        nodes = _filter_instances(compute_client, resource_group, filters)
        with pool.ThreadPool() as p:
            p.starmap(delete_virtual_machine,
                      [(resource_group, node.name) for node in nodes])
        return

    assert provider_config is not None, cluster_name_on_cloud

    resource_group_client = azure.get_client('resource', subscription_id)
    delete_resource_group = _get_azure_sdk_function(
        client=resource_group_client.resource_groups, function_name='delete')

    try:
        delete_resource_group(resource_group, force_deletion_types=None)
    except azure.exceptions().ResourceNotFoundError as e:
        if 'ResourceGroupNotFound' in str(e):
            logger.warning(f'Resource group {resource_group} not found. Skip '
                           'terminating it.')
            return
        raise


def _get_instance_status(
        compute_client: 'azure_compute.ComputeManagementClient', vm,
        resource_group: str) -> Optional[AzureInstanceStatus]:
    try:
        instance = compute_client.virtual_machines.instance_view(
            resource_group_name=resource_group, vm_name=vm.name)
    except azure.exceptions().ResourceNotFoundError as e:
        if 'ResourceNotFound' in str(e):
            return None
        raise
    provisioning_state = vm.provisioning_state
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
            return AzureInstanceStatus.from_raw_states(provisioning_state,
                                                       state)
    return AzureInstanceStatus.from_raw_states(provisioning_state, None)


def _filter_instances(
    compute_client: 'azure_compute.ComputeManagementClient',
    resource_group: str,
    tag_filters: Dict[str, str],
    status_filters: Optional[List[AzureInstanceStatus]] = None,
    included_instances: Optional[List[str]] = None,
) -> List['azure_compute.models.VirtualMachine']:

    def match_tags(vm):
        for k, v in tag_filters.items():
            if vm.tags.get(k) != v:
                return False
        return True

    try:
        list_virtual_machines = _get_azure_sdk_function(
            client=compute_client.virtual_machines, function_name='list')
        vms = list_virtual_machines(resource_group_name=resource_group)
        nodes = list(filter(match_tags, vms))
    except azure.exceptions().ResourceNotFoundError as e:
        if _RESOURCE_GROUP_NOT_FOUND_ERROR_MESSAGE in str(e):
            return []
        raise
    if status_filters is not None:
        nodes = [
            node for node in nodes if _get_instance_status(
                compute_client, node, resource_group) in status_filters
        ]
    if included_instances:
        nodes = [node for node in nodes if node.name in included_instances]
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
    nodes = _filter_instances(compute_client, resource_group, filters)
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}

    def _fetch_and_map_status(node, resource_group: str) -> None:
        compute_client = azure.get_client('compute', subscription_id)
        status = _get_instance_status(compute_client, node, resource_group)

        if status is None and non_terminated_only:
            return
        statuses[node.name] = (None if status is None else
                               status.to_cluster_status())

    with pool.ThreadPool() as p:
        p.starmap(_fetch_and_map_status,
                  [(node, resource_group) for node in nodes])

    return statuses


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

    update_network_security_groups = _get_azure_sdk_function(
        client=network_client.network_security_groups,
        function_name='create_or_update')
    list_network_security_groups = _get_azure_sdk_function(
        client=network_client.network_security_groups, function_name='list')
    for nsg in list_network_security_groups(resource_group):
        try:
            # Wait the NSG creation to be finished before opening a port. The
            # cluster provisioning triggers the NSG creation, but it may not be
            # finished yet.
            backoff = common_utils.Backoff(max_backoff_factor=1)
            start_time = time.time()
            while True:
                if nsg.provisioning_state not in ['Creating', 'Updating']:
                    break
                if time.time() - start_time > _WAIT_CREATION_TIMEOUT_SECONDS:
                    logger.warning(
                        f'Fails to wait for the creation of NSG {nsg.name} in '
                        f'{resource_group} within '
                        f'{_WAIT_CREATION_TIMEOUT_SECONDS} seconds. '
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

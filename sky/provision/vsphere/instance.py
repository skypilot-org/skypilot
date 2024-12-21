"""Vsphere instance provisioning."""
import json
import os
import typing
from typing import Any, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.adaptors import common as adaptors_common
from sky.adaptors import vsphere as vsphere_adaptor
from sky.clouds.service_catalog.common import get_catalog_path
from sky.provision import common
from sky.provision.vsphere import vsphere_utils
from sky.provision.vsphere.common import custom_script as custom_script_lib
from sky.provision.vsphere.common import metadata_utils
from sky.provision.vsphere.common.vim_utils import poweroff_vm
from sky.provision.vsphere.common.vim_utils import wait_for_tasks
from sky.provision.vsphere.common.vim_utils import wait_internal_ip_ready
from sky.provision.vsphere.vsphere_utils import VsphereClient

if typing.TYPE_CHECKING:
    import pandas as pd
else:
    pd = adaptors_common.LazyImport('pandas')

logger = sky_logging.init_logger(__name__)

TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_SKYPILOT_HEAD_NODE = 'skypilot-head-node'
HEAD_NODE_VALUE = '1'
WORKER_NODE_VALUE = '0'
PUBLIC_SSH_KEY_PATH = '~/.ssh/sky-key.pub'


def run_instances(region: str, cluster_name: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    logger.info('New provision of Vsphere: run_instances().')

    resumed_instance_ids: List[str] = []
    created_instance_ids: List[str] = []
    vc_object = _get_vc_object(region)
    vc_object.connect()

    exist_instances = _get_filtered_instance(vc_object, cluster_name,
                                             config.provider_config)
    head_instance_id = _get_head_instance_id(exist_instances)

    running_instances = []
    stopped_instances = []

    for inst in exist_instances:
        if inst.runtime.powerState == 'poweredOn':
            # poweredOn state
            running_instances.append(inst)
        else:
            # poweredOff or suspended state
            stopped_instances.append(inst)

    # Running instances will be selected first
    # example: UP --> sky stop --> powerOn worker node manually
    if head_instance_id is None and running_instances:
        head_instance_id = running_instances[0].summary.config.instanceUuid

    to_start_num = config.count - len(running_instances)
    # Then the stopped instances will be selected if the number of
    # running instances are not enough.
    if config.resume_stopped_nodes and to_start_num > 0 and stopped_instances:
        resumed_instances = stopped_instances[:to_start_num]
        # Power on all the resumed_instances
        wait_for_tasks(vc_object.servicemanager.content,
                       [vm.PowerOn() for vm in resumed_instances])
        for resumed_inst in resumed_instances:
            wait_internal_ip_ready(resumed_inst)
            resumed_instance_ids.append(
                resumed_inst.summary.config.instanceUuid)
        # example: UP --> sky stop --> delete head node or remove
        # head tag manually
        if head_instance_id is None:
            head_instance_id = resumed_instance_ids[0]
        to_start_num -= len(resumed_instances)

    vsphere_cluster_name = None
    if to_start_num > 0:
        # If there are already running or resumed instances,
        # then we can't guarantee that all the instances will
        # be created in the same availability vsphere cluster
        # when there are multiple vsphere clusters specified,
        # This is a known issue.
        vsphere_cluster_name = _choose_vsphere_cluster_name(
            config, region, vc_object)
        # TODO: update logic for multi-node creation
        for _ in range(to_start_num):
            created_instance_uuid = _create_instances(cluster_name, config,
                                                      region, vc_object,
                                                      vsphere_cluster_name)
            created_instance_ids.append(created_instance_uuid)
        if head_instance_id is None:
            head_instance_id = created_instance_ids[0]

    head_tag = [{'Key': TAG_SKYPILOT_HEAD_NODE, 'Value': HEAD_NODE_VALUE}]
    vc_object.set_tags(head_instance_id, head_tag)

    vc_object.disconnect()
    return common.ProvisionRecord(
        provider_name='vsphere',
        region=region,
        zone=vsphere_cluster_name,
        cluster_name=cluster_name,
        head_instance_id=head_instance_id,
        resumed_instance_ids=resumed_instance_ids,
        created_instance_ids=created_instance_ids,
    )


def _create_instances(
    cluster_name: str,
    config: common.ProvisionConfig,
    region: str,
    vc_object: VsphereClient,
    vsphere_cluster_name: str,
):
    """Create vsphere vsphere_adaptor.get_vcenter_client().VM instances"""
    lib_item_id = None
    # Read hosts, vms and mapping information
    image_instance_mapping_df = pd.read_csv(
        get_catalog_path('vsphere/instance_image_mapping.csv'),
        encoding='utf-8')
    hosts_df = pd.read_csv(get_catalog_path('vsphere/hosts.csv'),
                           encoding='utf-8')
    vms_df = pd.read_csv(get_catalog_path('vsphere/vms.csv'), encoding='utf-8')
    images_df = pd.read_csv(get_catalog_path('vsphere/images.csv'),
                            encoding='utf-8')

    # First, filter all dfs with the region.
    # Note: vms's Region --> vcenter's name, and vms's AvailabilityZone -->
    # vcenter's cluster name.
    hosts_df = hosts_df[(hosts_df['vCenter'] == region) &
                        (hosts_df['Cluster'] == vsphere_cluster_name)]
    vms_df = vms_df[(vms_df['Region'] == region) &
                    (vms_df['AvailabilityZone'] == vsphere_cluster_name)]
    image_instance_mapping_df = image_instance_mapping_df[
        image_instance_mapping_df['vCenter'] == region]

    # Get instance type from node config
    instance_type = config.node_config.get('InstanceType', None)

    # Instance type must not be empty
    assert instance_type is not None, (f'InstanceType {instance_type} in '
                                       f'node_config is not specified or '
                                       f'available.')
    vms_df = vms_df[vms_df['InstanceType'] == instance_type]

    # Check if the user want to create a cpu or a gpu instance by checking if
    # the AcceleratorName of the instance is empty
    gpu_instance = False
    if not pd.isna(vms_df.iloc[0]['AcceleratorName']):
        gpu_instance = True

    # Check if the user want to create a cpu instance
    if not gpu_instance:
        # Find an image for CPU
        images_df = images_df[images_df['GpuTags'] == '\'[]\'']
        if not images_df:
            logger.error(
                f'Can not find an image for instance type: {instance_type}.')
            raise Exception(
                f'Can not find an image for instance type: {instance_type}.')
        elif len(images_df) > 1:
            logger.warning(
                f"""There are multiple images can match instance type named
                {instance_type}.
                This means you have multiple images with the same tag
                'SKYPILOT-CPU'.
                We will use the first one to create the instance.""")
        images_df = images_df.loc[[0]]
        image_item = images_df.iloc[0].to_dict()
        lib_item_id = image_item['ImageID']

    elif gpu_instance:
        gpu = None
        # Use the instance type name to filter mapping df and vms df to
        # find image for GPU
        image_instance_mapping_df = image_instance_mapping_df[
            image_instance_mapping_df['InstanceType'] == instance_type]

        if not image_instance_mapping_df:
            raise Exception(f"""There is no image can match instance type named
                {instance_type}
                If you are using CPU-only instance, assign an image with tag
                'SKYPILOT-CPU'
                If you are using GPU instance, assign an image with tag
                'GPU-$your_gpu_short_name$' like GPU-A100
                Or tag 'SKYPILOT-$manufacturer_short_name$' like
                SKYPILOT-NVIDIA.""")
        image_instance_mapping_item = (
            image_instance_mapping_df.iloc[0].to_dict())

        # Get the lib item id from the mapping df, we will use this id to
        # get the best host to create the instance
        lib_item_id = image_instance_mapping_item['ImageID']

    assert lib_item_id is not None, (
        f'Failed to get the lib item id for instance type {instance_type}.')

    # There may be multiple items in the vms df, we will use the first
    # one to create the instance
    # TODO: make sure the items in vms.csv are unique by InstanceType, for
    #  , we will use the first one
    vms_df = vms_df.iloc[[0]]
    vms_item = vms_df.iloc[0].to_dict()

    # Filter the hosts df with CPU and Memory to make sure the host has
    # enough resource to create the instance
    cpus_needed = int(vms_item['vCPUs'])
    memory_needed = int(vms_item['MemoryGiB'] * 1024)
    hosts_df = hosts_df[(hosts_df['AvailableCPUs'] /
                         hosts_df['cpuMhz']) >= cpus_needed]
    hosts_df = hosts_df[hosts_df['AvailableMemory(MB)'] >= memory_needed]
    assert hosts_df, (f'There is no host available to create the instance '
                      f'{vms_item["InstanceType"]}, at least {cpus_needed} '
                      f'cpus and {memory_needed}MB memory are required.')

    # Sort the hosts df by AvailableCPUs to get the compatible host with the
    # least resource
    hosts_df = hosts_df.sort_values(by=['AvailableCPUs'], ascending=False)

    # First deal with the cpu instance, a cpu instance's name is like
    # 'cpu.xlarge' that starts with 'cpu.' TODO: add support for the cpu
    #  instances that are not started with 'cpu.'
    hosts_item = None
    # gpu_item = None
    if not gpu_instance:
        # Get the first host that has enough cpus
        hosts_item = hosts_df.iloc[0].to_dict()
        host_mobid = hosts_item['MobID']
    elif gpu_instance:
        # Get the host that has the same gpu as the instance type
        # TODO: add support for multiple-gpu instance
        for _, row in hosts_df.iterrows():
            gpus = row['GPU']
            if gpus != '[]':
                # TODO: improve the csv initialization logic, for now,
                #  we need to replace the single quote with double quote
                gpus = json.loads(gpus.replace('\'', '\"'))
                for gpu in gpus:
                    if gpu.get('Status') == 'Available':
                        if (vms_item['AcceleratorName'].lower()
                                in gpu.get('DeviceName').lower()):
                            hosts_item = row.to_dict()
                            # gpu_item = gpu
                            break
            if hosts_item:
                break
        assert hosts_item is not None, (f'There is no host available to create '
                                        f'the instance '
                                        f'{vms_item["InstanceType"]}, '
                                        f'no host has the same gpu as '
                                        f'the instance type.')
        host_mobid = hosts_item['MobID']
    else:
        raise Exception(f'Instance type {instance_type} is not supported.')
    spec = vsphere_adaptor.get_vim().vm.ConfigSpec()
    spec.memoryMB = memory_needed
    spec.numCPUs = cpus_needed
    spec.memoryAllocation = vsphere_adaptor.get_vim().ResourceAllocationInfo(
        reservation=spec.memoryMB)
    if gpu_instance and gpu:
        device_id = gpu.get('DeviceID')
        vendor_id = gpu.get('VendorID')
        pci_device_spec = vsphere_adaptor.get_vim().vm.device.VirtualDeviceSpec(
        )
        pci_device_spec.operation = (
            vsphere_adaptor.get_vim().vm.device.VirtualDeviceSpec.Operation.add)

        backing = vsphere_adaptor.get_vim(
        ).vm.device.VirtualPCIPassthrough.DynamicBackingInfo()
        allowed_device = vsphere_adaptor.get_vim(
        ).vm.device.VirtualPCIPassthrough.AllowedDevice()
        allowed_device.deviceId = int(device_id, 16)
        allowed_device.vendorId = int(vendor_id, 16)
        backing.allowedDevice.append(allowed_device)

        pci_device = vsphere_adaptor.get_vim().vm.device.VirtualPCIPassthrough()
        pci_device.backing = backing
        pci_device_spec.device = pci_device
        spec.deviceChange = [pci_device_spec]

        # if the gpu is a high-end card with 16 and more GPU memeory,
        # we should set the use64bitMMIO=true
        # the 64bitMMIOSizeGB will be x * 16 *2  where x the is number
        # of GPU, here is will be 1
        if spec.memoryMB >= 16 * 1024:
            use64mmio = vsphere_adaptor.get_vim().OptionValue()  # type: ignore
            use64mmio.key = 'pciPassthru.use64bitMMIO'
            use64mmio.value = 'TRUE'
            mmiosizegb = vsphere_adaptor.get_vim().OptionValue()  # type: ignore
            mmiosizegb.key = 'pciPassthru.64bitMMIOSizeGB'
            mmiosizegb.value = int(spec.memoryMB * 2 / 1024)
            spec.extraConfig = [use64mmio, mmiosizegb]

    # Create the customization spec
    # Set up the VM's authorized_keys with customization spec
    ssh_key_path = os.path.expanduser(PUBLIC_SSH_KEY_PATH)
    if not os.path.exists(ssh_key_path):
        logger.error('SSH pubic key does not exist.')
        raise exceptions.ResourcesUnavailableError(
            'SSH pubic key does not exist.')
    with open(ssh_key_path, 'r', encoding='utf-8') as f:
        ssh_public_key = f.read()

    # Create a custom script to inject the ssh public key into the instance
    vm_user = config.authentication_config['ssh_user']
    custom_script = custom_script_lib.CUSTOMIZED_SCRIPT.replace(
        'ssh_public_key', ssh_public_key)
    custom_script = custom_script.replace('user_placeholder', vm_user)
    created_instance_uuid = vc_object.create_instances(
        cluster=vsphere_cluster_name,
        host_mobid=host_mobid,
        lib_item_id=lib_item_id,
        spec=spec,
        customization_spec_str=custom_script,
    )
    if created_instance_uuid is None:
        logger.error(f'Failed to create the instance on host {host_mobid} in '
                     f'{vsphere_cluster_name} with instance type:'
                     f'{vms_item["InstanceType"]}.')
        instance_type = vms_item['InstanceType']
        raise Exception(f'Failed to create the instance on host {host_mobid} '
                        f'in {vsphere_cluster_name} with instance type:'
                        f'{instance_type}.')

    # Store instance uuid in local file
    cluster_info = metadata_utils.Metadata()
    new_cache_value = [created_instance_uuid]
    old_cache_value = cluster_info.get(cluster_name)
    if old_cache_value:
        new_cache_value.extend(old_cache_value)
    cluster_info.set(cluster_name, new_cache_value)
    cluster_info.save()

    # TODO: add logic to remove certain resource from vms.csv
    #  and hosts.csv after creation.

    tags = [
        {
            'Key': TAG_SKYPILOT_CLUSTER_NAME,
            'Value': cluster_name
        },
        {
            'Key': TAG_SKYPILOT_HEAD_NODE,
            'Value': WORKER_NODE_VALUE
        },
    ]

    vc_object.set_tags(created_instance_uuid, tags)
    return created_instance_uuid


def _choose_vsphere_cluster_name(config: common.ProvisionConfig, region: str,
                                 vc_object: VsphereClient):
    """Select a vSphere cluster name using user-configured clusters and
    skypilot framework-optimized availability_zones"""
    vsphere_cluster_name = None
    vsphere_cluster_name_str = config.provider_config['availability_zone']
    if vc_object.clusters:
        for optimized_cluster_name in vsphere_cluster_name_str.split(','):
            if optimized_cluster_name in [
                    item['name'] for item in vc_object.clusters
            ]:
                vsphere_cluster_name = optimized_cluster_name
                break
        assert vsphere_cluster_name is not None, (f'The select cluster '
                                                  f'is not allowed to used in '
                                                  f'vcenter: {region}, '
                                                  f'Will try next cluster.')
    else:
        vsphere_cluster_name = vsphere_cluster_name_str.split(',')[0]
    return vsphere_cluster_name


def _get_vc_object(region):
    # Get credential
    vcenter = vsphere_utils.get_vsphere_credentials(region)
    # Create VsphereClient
    skip_key = 'skip_verification'
    if skip_key not in vcenter:
        vcenter[skip_key] = False
    vc_object = vsphere_utils.VsphereClient(
        vcenter['name'],
        vcenter['username'],
        vcenter['password'],
        vcenter['clusters'],
        vcenter[skip_key],
    )
    return vc_object


def _get_cluster_name_filter(cluster_name_on_cloud):
    return [{'Key': TAG_SKYPILOT_CLUSTER_NAME, 'Value': cluster_name_on_cloud}]


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    logger.info('New provision of Vsphere: query_instances().')
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    vc_object = _get_vc_object(region)
    vc_object.connect()

    instances = _get_filtered_instance(vc_object, cluster_name_on_cloud,
                                       provider_config)

    status_map = {
        'poweredOff': status_lib.ClusterStatus.STOPPED,
        'poweredOn': status_lib.ClusterStatus.UP,
        'suspended': None,
    }

    status = {}
    for inst in instances:
        stat = status_map[inst.runtime.powerState]
        if non_terminated_only and stat is None:
            continue
        status[inst.summary.config.instanceUuid] = stat
    vc_object.disconnect()
    return status


def _get_filtered_instance(
    vc_object,
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
):
    # Get instance uuid in cache file
    cluster_info = metadata_utils.Metadata()
    cached_inst_ids = cluster_info.get(cluster_name_on_cloud)
    # If cached_inst_ids is None of empty, means no VM instance exist
    # in the cluster,
    # and no further processing is required
    if not cached_inst_ids:
        return []
    # Get filter
    filters = _get_cluster_name_filter(cluster_name_on_cloud)
    if worker_only:
        filters.append({
            'Key': TAG_SKYPILOT_HEAD_NODE,
            'Value': WORKER_NODE_VALUE,
        })
    # Get vsphere cluster
    vsphere_cluster_name_str = provider_config['availability_zone']
    vsphere_clusters = vsphere_cluster_name_str.split(',')

    instances = vc_object.filter_instances(cached_inst_ids, filters,
                                           vsphere_clusters)
    return instances


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    logger.info('New provision of Vsphere: stop_instances().')
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    vc_object = _get_vc_object(region)
    vc_object.connect()
    instances = _get_filtered_instance(vc_object, cluster_name_on_cloud,
                                       provider_config, worker_only)
    if not instances:
        return
    # Power off vsphere_adaptor.get_vcenter_client().VM
    for inst in instances:
        if inst.runtime.powerState != 'poweredOff':
            poweroff_vm(vc_object.servicemanager.content, inst)
    vc_object.disconnect()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    logger.info('New provision of Vsphere: terminate_instances().')
    assert provider_config is not None, cluster_name_on_cloud
    region = provider_config['region']
    vc_object = _get_vc_object(region)
    vc_object.connect()
    instances = _get_filtered_instance(vc_object, cluster_name_on_cloud,
                                       provider_config, worker_only)
    if not instances:
        return
    vm_service = vsphere_adaptor.get_vcenter_client().VM(
        vc_object.servicemanager.stub_config)
    for inst in instances:
        if inst.runtime.powerState == 'poweredOn':
            poweroff_vm(vc_object.servicemanager.content, inst)
        vm_service.delete(inst._moId)  # pylint: disable=protected-access
    # Clear the cache when down the cluster
    cluster_info = metadata_utils.Metadata()
    cluster_info.pop(cluster_name_on_cloud)
    cluster_info.save()
    vc_object.disconnect()


def wait_instances(region: str, cluster_name: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py"""
    logger.info(f'New provision of Vsphere: wait_instances().'
                f'{region} {cluster_name} {state}')
    pass


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    logger.info(f'New provision of Vsphere: open_ports(). '
                f'{cluster_name_on_cloud}'
                f'{ports}'
                f'{provider_config}')
    pass


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    logger.info(f'New provision of Vsphere: cleanup_ports().'
                f'{cluster_name_on_cloud} {provider_config}')
    pass


def _get_head_instance_id(instances):
    head_instance_id = None
    head_node_filter = {
        'Key': TAG_SKYPILOT_HEAD_NODE,
        'Value': HEAD_NODE_VALUE,
    }
    for inst in instances:
        cust_attributes = [(f.name, v.value) for f in inst.availableField
                           if f.name == head_node_filter['Key']
                           for v in inst.customValue if f.key == v.key]
        if ((head_node_filter['Key'], head_node_filter['Value'])
                in cust_attributes):
            if head_instance_id is not None:
                logger.warning(f'Multiple head nodes exist in the cluster'
                               f'The current head node id is: '
                               f'{head_instance_id}'
                               f'The newly found head node id is: '
                               f'{inst.summary.config.instanceUuid}')
            head_instance_id = inst.summary.config.instanceUuid
    return head_instance_id


def get_cluster_info(
        region: str,
        cluster_name: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """See sky/provision/__init__.py"""
    logger.info('New provision of Vsphere: get_cluster_info().')

    # Init the vsphere client
    vc_object = _get_vc_object(region)
    vc_object.connect()

    filters = _get_cluster_name_filter(cluster_name)
    # Get instance uuid in cache file
    cluster_info = metadata_utils.Metadata()
    cached_inst_ids = cluster_info.get(cluster_name)
    # If cached_inst_ids is None of empty, means no VM instance exist
    # in the cluster,
    # An empty ClusterInfo will return.
    vm_objs = []
    if cached_inst_ids:
        vm_objs = vc_object.filter_instances(cached_inst_ids, filters)
    # Find instances and head_instance_id
    instances = {}
    for vm in vm_objs:
        if vm.runtime.powerState != 'poweredOn':
            continue
        instances[vm.summary.config.instanceUuid] = [
            common.InstanceInfo(
                instance_id=vm.summary.config.instanceUuid,
                internal_ip=vm.summary.guest.ipAddress,
                external_ip=None,
                tags={},
            )
        ]

    # Get head node id
    head_instance_id = _get_head_instance_id(vm_objs)

    vc_object.disconnect()
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='vsphere',
        provider_config=provider_config,
    )

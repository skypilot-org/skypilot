"""VIM Utils
"""

import re
import subprocess
import time
from typing import List

from sky import sky_logging
from sky.adaptors import vsphere as vsphere_adaptor
from sky.clouds.service_catalog.data_fetchers.fetch_vsphere import (
    get_accelerators_from_csv)

logger = sky_logging.init_logger(__name__)
DISPLAY_CONTROLLER_CLASS_ID_PREFIXES = ['03']
VMWARE_VIRTUAL_DISPLAY_CONTROLLER_IDS = ['0000:00:0f.0']


def get_objs_by_names(content, vimtype: type, names: List[str]):
    """    Get the vsphere managed object associated with a given text name
    """
    # Create a set for the names for faster lookups
    names_set = set(names)
    objs = {}
    container = content.viewManager.CreateContainerView(content.rootFolder,
                                                        vimtype, True)
    for entity in container.view:
        if entity.name in names_set:
            objs[entity.name] = entity
            names_set.remove(entity.name)

    container.Destroy()

    # If there are any names left in the names_set, those were not found
    if names_set:
        for name in names_set:
            logger.error(f'Object \'{name}\' not found')
        raise RuntimeError(f'Only {len(objs)} out of {len(names)} '
                           f'objects found')

    return objs


def get_hosts_by_cluster_names(content, vcenter_name, cluster_name_dicts=None):
    """    Get a list of hosts in the vCenter Server by cluster names
    """
    if cluster_name_dicts is None:
        cluster_name_dicts = [{}]
    hosts = []
    gpu_list = get_accelerators_from_csv()
    if not cluster_name_dicts:
        cluster_view = content.viewManager.CreateContainerView(
            content.rootFolder,
            [vsphere_adaptor.get_vim().ClusterComputeResource], True)
        cluster_name_dicts = [{
            'name': cluster.name
        } for cluster in cluster_view.view]
        cluster_view.Destroy()
        if not cluster_name_dicts:
            logger.warning(f'vCenter \'{vcenter_name}\' has no clusters')

    # Retrieve all cluster names from the cluster_name_dicts
    cluster_names = [
        cluster_dict['name'] for cluster_dict in cluster_name_dicts
    ]

    # Use get_objs to retrieve all cluster objects at once
    cluster_obj_dict = get_objs_by_names(
        content, [vsphere_adaptor.get_vim().ClusterComputeResource],
        cluster_names)

    for cluster_name_dict in cluster_name_dicts:
        cluster_name = cluster_name_dict.get('name')
        cluster_obj = cluster_obj_dict.get(cluster_name)
        if cluster_obj:
            datacenter_name = get_datacenter_name(cluster_obj)
            hosts_info = list_hosts_with_devices_info(cluster_obj.host,
                                                      vcenter_name,
                                                      datacenter_name,
                                                      cluster_name, gpu_list)
            hosts.extend(hosts_info)
        else:
            logger.warning(f'Cluster \'{cluster_name}\' not found')
    return hosts


def get_datacenter_name(cluster_obj):
    datacenter_name = None
    parent = cluster_obj.parent
    while parent:
        if isinstance(parent, vsphere_adaptor.get_vim().Datacenter):
            datacenter_name = parent.name
            break
        parent = parent.parent
    return datacenter_name


def filter_vms_by_cluster_name(content, cluster_names=None):
    """    Get a list of vms in the vCenter clusters
    If cluster_names is empty or None, then get all VMs in vCenter
    """
    instances = []
    container = content.viewManager.CreateContainerView(
        content.rootFolder, [vsphere_adaptor.get_vim().ClusterComputeResource],
        True)
    cluster_objs = container.view
    container.Destroy()
    # Filter the target cluster, and get the VMs in cluster
    for cluster in cluster_objs:
        if cluster_names and cluster.name not in cluster_names:
            continue
        for host in cluster.host:
            vms = host.vm
            instances.extend(vms)
    return instances


def list_hosts_with_devices_info(hosts, vcenter_name, datacenter_name,
                                 cluster_name, gpu_list):

    def decimal_to_four_char_hex(decimal):
        result = str(hex(decimal)).replace('0x', '').zfill(4)
        return f'{result}'

    all_hosts_with_devices = []

    # Get the host information
    for host in hosts:
        hw = host.hardware
        system_info = hw.systemInfo
        cpu_info = hw.cpuInfo
        memory_info_mb = round(hw.memorySize / (1024**2), 2)

        # Get available memory and CPU
        host_summary = host.summary
        hw_summary = host_summary.hardware
        quick_stats = host_summary.quickStats
        if quick_stats.overallCpuUsage is None:
            # This mean the host is disconnected
            continue

        # Calculate available CPU and Memory based on host.summary
        cpu_mhz_info = hw_summary.cpuMhz
        total_cpu_mhz = cpu_mhz_info * cpu_info.numCpuCores
        available_cpu_mhz = total_cpu_mhz - quick_stats.overallCpuUsage
        available_memory_mb = round(
            memory_info_mb - quick_stats.overallMemoryUsage, 2)

        vms_list = host.vm
        used_device_ids = []

        # TODO: Test support for multiple GPUs on single host
        for vm in vms_list:
            vm_status = vm.runtime.powerState
            if vm_status == 'poweredOn':
                for device in vm.config.hardware.device:
                    if isinstance(
                            device,
                            vsphere_adaptor.get_vim().vm.device.
                            VirtualPCIPassthrough):
                        if hasattr(device, 'backing') and isinstance(
                                device.backing,
                                vsphere_adaptor.get_vim().vm.device.
                                VirtualPCIPassthrough.DynamicBackingInfo,
                        ):
                            used_device_ids.append(device.backing.assignedId)

        host_info = {
            'HostName': host.name,
            'Model': system_info.model,
            'Vendor': system_info.vendor,
            'UUID': system_info.uuid,
            'TotalCPUcores': cpu_info.numCpuCores,
            'TotalCPUMHz': total_cpu_mhz,
            'CPUMHz': cpu_mhz_info,
            'TotalCPUpackages': cpu_info.numCpuPackages,
            'TotalCPUthreads': cpu_info.numCpuThreads,
            'AvailableCPUCapacityMHz': available_cpu_mhz,
            'TotalMemoryMB': memory_info_mb,
            'AvailableMemoryMB': available_memory_mb,
            'Cluster': cluster_name,
            'Datacenter': datacenter_name,
            'vCenter': vcenter_name,
            'MoRefID': host._moId,  # pylint: disable=protected-access
            'Accelerators': [],
            'GpuInfo': None,
        }
        # Retrieve PCI devices information
        pci_devices = host.hardware.pciDevice
        pci_passthru_devices = host.config.pciPassthruInfo
        for device, passthru_info in zip(pci_devices, pci_passthru_devices):
            if (decimal_to_four_char_hex(device.classId)[:2]
                    in DISPLAY_CONTROLLER_CLASS_ID_PREFIXES and
                    device.id not in VMWARE_VIRTUAL_DISPLAY_CONTROLLER_IDS):
                # Get the device status
                if passthru_info.passthruActive:
                    if device.id in used_device_ids:
                        device_status = 'Busy'
                    else:
                        device_status = 'Available'
                else:
                    device_status = 'Unavailable'
                memory_info = {'SizeInMiB': 0}
                for preset_accelerator in gpu_list:
                    if preset_accelerator['Model'].lower(
                    ) in device.deviceName.lower():
                        memory_info = {
                            'SizeInMiB': preset_accelerator['MemoryMB']
                        }
                        break
                device_info = {
                    'ID': device.id,
                    'ClassID': decimal_to_four_char_hex(device.classId),
                    'VendorID': decimal_to_four_char_hex(device.vendorId),
                    'VendorName': device.vendorName,
                    'DeviceID': decimal_to_four_char_hex(device.deviceId),
                    'DeviceName': device.deviceName,
                    'SubVendorID': decimal_to_four_char_hex(device.subVendorId),
                    'SubDeviceID': decimal_to_four_char_hex(device.subDeviceId),
                    'Status': device_status,
                    'MemorySizeMB': memory_info['SizeInMiB'],
                }
                host_info['Accelerators'].append(device_info)

                # GpuInfo is for vms.csv, which only needs one entry for each
                # host
                # Always put the GPU info into the vms.csv (
                # there are some inconsistent
                # So we should check the host's accelerators filed for the GPU
                # status
                if not host_info['GpuInfo']:
                    host_info['GpuInfo'] = {
                        'Gpus': [{
                            'Name': device.deviceName,
                            # TODO: Make count field dynamic according
                            #  to the state of the device
                            'Count': 1,
                            'Manufacturer': device.vendorName,
                            'MemoryInfo': memory_info,
                        }],
                        'TotalGpuMemoryInMiB': memory_info['SizeInMiB'],
                    }
        all_hosts_with_devices.append(host_info)
    return all_hosts_with_devices


def get_obj_by_mo_id(content, vimtype, moid):
    """    Get the vsphere managed object by moid value
    """
    obj = None
    container = content.viewManager.CreateContainerView(content.rootFolder,
                                                        vimtype, True)
    for c in container.view:
        if c._GetMoId() == moid:  # pylint: disable=protected-access
            obj = c
            break
    container.Destroy()
    return obj


def delete_object(content, mo):
    """    Deletes a vsphere managed object and
    waits for the deletion to complete
    """
    logger.info('Deleting {0}'.format(mo._GetMoId()))  # pylint: disable=protected-access
    try:
        wait_for_tasks(content, [mo.Destroy()])
        logger.info('Deleted {0}'.format(mo._GetMoId()))  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-except
        logger.info('Unexpected error while deleting managed object {0}'.format(
            mo._GetMoId()))  # pylint: disable=protected-access
        return False
    return True


def poweron_vm(content, mo):
    """    Powers on a VM and wait for power on operation to complete
    """
    if not isinstance(mo, vsphere_adaptor.get_vim().VirtualMachine):
        return False

    logger.info('Powering on vm {0}'.format(mo._GetMoId()))  # pylint: disable=protected-access
    try:
        wait_for_tasks(content, [mo.PowerOn()])
        logger.info('{0} powered on successfully'.format(mo._GetMoId()))  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-except
        logger.info('Unexpected error while powering on vm {0}'.format(
            mo._GetMoId()))  # pylint: disable=protected-access
        return False
    return True


def wait_internal_ip_ready(instance):
    # 4-min maximum timeout
    logger.info('Wait for IP to be ready.')
    timeout = 60 * 4
    start = time.time()
    internal_ip = None
    while True:
        if instance.summary.guest is not None:
            # The ip can still be None after this assignment
            internal_ip = instance.summary.guest.ipAddress
        if internal_ip:
            # The ip will be IPv6 at first, then be replaced by IPv4,
            # we need to wait for IPv4 which is a sign of
            # a ready instance
            if '.' in internal_ip and ':' not in internal_ip:
                logger.info(f'The IP is ready: {internal_ip}.')
                break
        if time.time() - start > timeout:
            raise RuntimeError(
                f'Failed to get the IP address after {timeout} seconds.')
        logger.debug('Retry internal ip in 5 second...')
        time.sleep(5)


def poweroff_vm(content, mo):
    """    Powers on a VM and wait for power on operation to complete
    """
    if not isinstance(mo, vsphere_adaptor.get_vim().VirtualMachine):
        return False

    logger.info('Powering off vm {0}'.format(mo._GetMoId()))  # pylint: disable=protected-access
    try:
        wait_for_tasks(content, [mo.PowerOff()])
        logger.info('{0} powered off successfully'.format(mo._GetMoId()))  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-except
        logger.info('Unexpected error while powering off vm {0}'.format(
            mo._GetMoId()))  # pylint: disable=protected-access
        return False
    return True


def wait_for_tasks(content, tasks):
    """    Given the tasks, it returns after all the tasks are complete
    """
    task_list = [str(task) for task in tasks]

    # Create filter
    obj_specs = [
        vsphere_adaptor.get_vmodl().query.PropertyCollector.ObjectSpec(obj=task)
        for task in tasks
    ]
    prop_spec = vsphere_adaptor.get_vmodl(
    ).query.PropertyCollector.PropertySpec(type=vsphere_adaptor.get_vim().Task,
                                           pathSet=[],
                                           all=True)
    filter_spec = vsphere_adaptor.get_vmodl(
    ).query.PropertyCollector.FilterSpec()
    filter_spec.objectSet = obj_specs
    filter_spec.propSet = [prop_spec]
    task_filter = content.propertyCollector.CreateFilter(filter_spec, True)

    try:
        version, state = None, None

        # Loop looking for updates till the state moves to a completed state.
        while len(task_list):
            update = content.propertyCollector.WaitForUpdates(
                version)  # type: ignore
            for filter_set in update.filterSet:
                for obj_set in filter_set.objectSet:
                    task = obj_set.obj
                    for change in obj_set.changeSet:
                        if change.name == 'info':
                            state = change.val.state  # type: ignore
                        elif change.name == 'info.state':
                            state = change.val
                        else:
                            continue

                        if not str(task) in task_list:
                            continue

                        if state == vsphere_adaptor.get_vim(
                        ).TaskInfo.State.success:
                            # Remove task from taskList
                            task_list.remove(str(task))
                        elif state == vsphere_adaptor.get_vim(
                        ).TaskInfo.State.error:
                            raise task.info.error
            # Move to next version
            version = update.version
    finally:
        if task_filter:
            task_filter.Destroy()


def get_cluster_name_by_id(self, content, name):
    cluster_obj = get_objs_by_names(
        content, [vsphere_adaptor.get_vim().ClusterComputeResource], [name])
    if cluster_obj is not None:
        self.mo_id = cluster_obj['name']._GetMoId()  # pylint: disable=protected-access
        logger.info('Cluster MoId: {0}'.format(self.mo_id))
    else:
        logger.info('Cluster: {0} not found'.format(self.cluster_name))


def create_spec_with_script(vm, script_string: str):
    hostname = vsphere_adaptor.get_vim().vm.customization.FixedName()
    hostname.name = vm.name
    identity = vsphere_adaptor.get_vim().vm.customization.LinuxPrep(
        hostName=hostname, domain='VMWare.com', scriptText=script_string)
    global_ip_settings = vsphere_adaptor.get_vim(
    ).vm.customization.GlobalIPSettings()
    ip = vsphere_adaptor.get_vim().vm.customization.DhcpIpGenerator()
    ip_settings = vsphere_adaptor.get_vim().vm.customization.IPSettings(ip=ip)
    nic_setting_maps = []
    for device in vm.config.hardware.device:
        if isinstance(device,
                      vsphere_adaptor.get_vim().vm.device.VirtualEthernetCard):
            nic_setting_map = vsphere_adaptor.get_vim(
            ).vm.customization.AdapterMapping(adapter=ip_settings)
            nic_setting_maps.append(nic_setting_map)
    return vsphere_adaptor.get_vim().vm.customization.Specification(
        globalIPSettings=global_ip_settings,
        identity=identity,
        nicSettingMap=nic_setting_maps,
    )


def get_vm_uuid_from_vm():
    result = subprocess.run(['sudo', 'dmidecode', '-t', '1'],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            check=True)

    if result.returncode != 0:
        print('Error running dmidecode: ', result.stderr)
        return None

    matches = re.search(r'Serial Number: VMware-(.+)', result.stdout)

    if matches:
        serial_number = matches.group(1).strip().replace(' ',
                                                         '').replace('-', '')
        if len(serial_number) != 32:
            logger.error(f'Invalid serial number: {serial_number}')
            raise RuntimeError('Failed to get UUID from dmidecode')
        uuid = (f'{serial_number[:8]}-{serial_number[8:12]}-'
                f'{serial_number[12:16]}-{serial_number[16:20]}-'
                f'{serial_number[20:]}')
        return uuid
    else:
        logger.error('Failed to get UUID from dmidecode')
        raise RuntimeError('Failed to get UUID from dmidecode')

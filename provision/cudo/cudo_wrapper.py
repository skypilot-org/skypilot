"""Cudo Compute library wrapper for SkyPilot."""
import time
from typing import Dict

from sky import sky_logging
from sky.adaptors import cudo
import sky.provision.cudo.cudo_utils as utils

logger = sky_logging.init_logger(__name__)


def launch(name: str, data_center_id: str, ssh_key: str, machine_type: str,
           memory_gib: int, vcpu_count: int, gpu_count: int,
           tags: Dict[str, str], disk_size: int):
    """Launches an instance with the given parameters."""

    request = cudo.cudo.CreateVMBody(
        ssh_key_source='SSH_KEY_SOURCE_NONE',
        custom_ssh_keys=[ssh_key],
        vm_id=name,
        machine_type=machine_type,
        data_center_id=data_center_id,
        boot_disk_image_id='ubuntu-2204-nvidia-535-docker-v20240214',
        memory_gib=memory_gib,
        vcpus=vcpu_count,
        gpus=gpu_count,
        boot_disk=cudo.cudo.Disk(storage_class='STORAGE_CLASS_NETWORK',
                                 size_gib=disk_size),
        metadata=tags)

    api = cudo.cudo.cudo_api.virtual_machines()
    vm = api.create_vm(cudo.cudo.cudo_api.project_id_throwable(), request)

    return vm.to_dict()['id']


def remove(instance_id: str):
    """Terminates the given instance."""
    terminate_ok = [
        'pend',
        'poff',
        'runn',
        'stop',
        'susp',
        'unde',
        'fail',
    ]
    api = cudo.cudo.cudo_api.virtual_machines()
    max_retries = 10
    retry_interval = 5
    retry_count = 0
    state = 'unknown'
    project_id = cudo.cudo.cudo_api.project_id_throwable()
    while retry_count < max_retries:
        vm = api.get_vm(project_id, instance_id)
        state = vm.to_dict()['vm']['short_state']

        if state in terminate_ok:
            break
        retry_count += 1
        time.sleep(retry_interval)
    else:
        raise Exception(
            'Timeout error, could not terminate due to VM state: {}'.format(
                state))

    api.terminate_vm(project_id, instance_id)


def set_tags(instance_id: str, tags: Dict):
    """Sets the tags for the given instance."""
    api = cudo.cudo.cudo_api.virtual_machines()
    api.update_vm_metadata(
        cudo.cudo.cudo_api.project_id(), instance_id,
        cudo.cudo.UpdateVMMetadataBody(
            metadata=tags,
            merge=True))  # TODO (skypilot team) merge or overwrite?


def get_instance(vm_id):
    api = cudo.cudo.cudo_api.virtual_machines()
    vm = api.get_vm(cudo.cudo.cudo_api.project_id_throwable(), vm_id)
    vm_dict = vm.to_dict()
    return vm_dict


def list_instances():
    api = cudo.cudo.cudo_api.virtual_machines()
    vms = api.list_vms(cudo.cudo.cudo_api.project_id_throwable())
    instances = {}
    for vm in vms.to_dict()['vms']:
        ex_ip = vm['external_ip_address']
        in_ip = vm['internal_ip_address']
        if not in_ip:
            in_ip = ex_ip
        instance = {
            # active_state, init_state, lcm_state, short_state
            'status': vm['short_state'],
            'tags': vm['metadata'],
            'name': vm['id'],
            'ip': ex_ip,
            'external_ip': ex_ip,
            'internal_ip': in_ip
        }
        instances[vm['id']] = instance
    return instances


def vm_available(to_start_count, gpu_count, gpu_model, data_center_id, mem,
                 cpus):
    gpu_model = utils.skypilot_gpu_to_cudo_gpu(gpu_model)
    api = cudo.cudo.cudo_api.virtual_machines()
    types = api.list_vm_machine_types2()
    types_dict = types.to_dict()
    machine_types = types_dict['machine_types']

    # Filter machine types based on requirements
    matching_types = []
    for machine_type in machine_types:
        # Check if this machine type matches our requirements
        if (machine_type['data_center_id'] == data_center_id and
                machine_type['gpu_model'] == gpu_model and
                machine_type['min_vcpu'] <= cpus <= machine_type.get(
                    'max_vcpu_free', float('inf')) and
                machine_type['min_memory_gib'] <= mem <= machine_type.get(
                    'max_memory_gib_free', float('inf'))):

            # Calculate available VMs based on resource constraints
            max_vms_by_vcpu = machine_type[
                'total_vcpu_free'] // cpus if cpus > 0 else float('inf')
            max_vms_by_memory = machine_type[
                'total_memory_gib_free'] // mem if mem > 0 else float('inf')
            max_vms_by_gpu = machine_type[
                'total_gpu_free'] // gpu_count if gpu_count > 0 else float(
                    'inf')

            available_vms = min(max_vms_by_vcpu, max_vms_by_memory,
                                max_vms_by_gpu)
            matching_types.append(available_vms)

    total_count = sum(matching_types)
    if total_count < to_start_count:
        raise Exception(
            'Too many VMs requested, try another gpu type or region')
    return total_count

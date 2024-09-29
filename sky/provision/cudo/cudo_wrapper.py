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

    try:
        api = cudo.cudo.cudo_api.virtual_machines()
        vm = api.create_vm(cudo.cudo.cudo_api.project_id_throwable(), request)
        return vm.to_dict()['id']
    except cudo.cudo.rest.ApiException as e:
        raise e


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
        try:
            vm = api.get_vm(project_id, instance_id)
            state = vm.to_dict()['vm']['short_state']
        except cudo.cudo.rest.ApiException as e:
            raise e

        if state in terminate_ok:
            break
        retry_count += 1
        time.sleep(retry_interval)
    else:
        raise Exception(
            'Timeout error, could not terminate due to VM state: {}'.format(
                state))

    try:
        api.terminate_vm(project_id, instance_id)
    except cudo.cudo.rest.ApiException as e:
        raise e


def set_tags(instance_id: str, tags: Dict):
    """Sets the tags for the given instance."""
    try:
        api = cudo.cudo.cudo_api.virtual_machines()
        api.update_vm_metadata(
            cudo.cudo.cudo_api.project_id(), instance_id,
            cudo.cudo.UpdateVMMetadataBody(
                metadata=tags,
                merge=True))  # TODO (skypilot team) merge or overwrite?
    except cudo.cudo.rest.ApiException as e:
        raise e


def get_instance(vm_id):
    try:
        api = cudo.cudo.cudo_api.virtual_machines()
        vm = api.get_vm(cudo.cudo.cudo_api.project_id_throwable(), vm_id)
        vm_dict = vm.to_dict()
        return vm_dict
    except cudo.cudo.rest.ApiException as e:
        raise e


def list_instances():
    try:
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
    except cudo.cudo.rest.ApiException as e:
        raise e


def vm_available(to_start_count, gpu_count, gpu_model, data_center_id, mem,
                 cpus):
    try:
        gpu_model = utils.skypilot_gpu_to_cudo_gpu(gpu_model)
        api = cudo.cudo.cudo_api.virtual_machines()
        types = api.list_vm_machine_types(mem,
                                          cpus,
                                          gpu=gpu_count,
                                          gpu_model=gpu_model,
                                          data_center_id=data_center_id)
        types_dict = types.to_dict()
        hc = types_dict['host_configs']
        total_count = sum(item['count_vm_available'] for item in hc)
        if total_count < to_start_count:
            raise Exception(
                'Too many VMs requested, try another gpu type or region')
        return total_count
    except cudo.cudo.rest.ApiException as e:
        raise e

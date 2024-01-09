"""Cudo Compute library wrapper for SkyPilot."""
import random
import string
from typing import Dict

from sky.adaptors.cudo import cudo

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


def generate_random_string(length):
    characters = string.ascii_lowercase + string.digits
    random_string = ''.join(random.choice(characters) for _ in range(length))
    return random_string


def launch(name: str, data_center_id: str, ssh_key: str, machine_type: str,
           memory_gib: int, vcpu_count: int, gpu_count: int, gpu_model: str,
           tags: Dict[str, str], disk_size: int):
    """Launches an instance with the given parameters."""
    disk = cudo().Disk(storage_class='STORAGE_CLASS_NETWORK',
                             size_gib=disk_size,
                             id=generate_random_string(10))

    request = cudo().CreateVMBody(
        ssh_key_source='SSH_KEY_SOURCE_NONE',
        custom_ssh_keys=[ssh_key],
        vm_id=name,
        machine_type=machine_type,
        data_center_id=data_center_id,
        boot_disk_image_id='ubuntu-nvidia-docker',
        memory_gib=memory_gib,
        vcpus=vcpu_count,
        gpus=gpu_count,
        gpu_model=gpu_model,
        boot_disk=disk,
        metadata=tags)

    try:
        api = cudo().cudo_api.virtual_machines()
        vm = api.create_vm(cudo().cudo_api.project_id(), request)
        return vm.to_dict()['id']
    except cudo().rest.ApiException as e:
        raise e


def remove(instance_id: str):
    """Terminates the given instance."""
    try:
        api = cudo().cudo_api.virtual_machines()
        api.terminate_vm(cudo().cudo_api.project_id(), instance_id)
    except cudo().rest.ApiException as e:
        raise e


def set_tags(instance_id: str, tags: Dict):
    """Sets the tags for the given instance."""
    try:
        api = cudo().cudo_api.virtual_machines()
        api.update_vm_metadata(
            cudo().cudo_api.project_id(), instance_id,
            cudo().UpdateVMMetadataBody(
                metadata=tags,
                merge=True))  # TODO (skypilot team) merge or overwrite?
    except cudo().rest.ApiException as e:
        raise e


def get_instance(vm_id):
    try:
        api = cudo().cudo_api.virtual_machines()
        vm = api.get_vm(cudo().cudo_api.project_id(), vm_id)
        vm_dict = vm.to_dict()
        return vm_dict
    except cudo().rest.ApiException as e:
        raise e


def list_instances():
    try:
        api = cudo().cudo_api.virtual_machines()
        vms = api.list_vms(cudo().cudo_api.project_id())
        instances = {}
        for vm in vms.to_dict()['vms']:
            instance = {
                # active_state, init_state, lcm_state, short_state
                'status': vm['short_state'],
                'tags': vm['metadata'],
                'name': vm['id'],
                'ip': vm['external_ip_address'],
                'external_ip': vm['external_ip_address'],
                'internal_ip': vm['internal_ip_address']
            }
            instances[vm['id']] = instance
        return instances
    except cudo().rest.ApiException as e:
        raise e

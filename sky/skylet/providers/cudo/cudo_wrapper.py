import random
import string
from typing import Dict

from cudo_compute import CreateVMBody
from cudo_compute import cudo_api
from cudo_compute import Disk
from cudo_compute import UpdateVMMetadataBody
from cudo_compute.rest import ApiException


def generate_random_string(length):
    characters = string.ascii_lowercase + string.digits
    random_string = ''.join(random.choice(characters) for _ in range(length))
    return random_string


def launch(name: str, data_center_id: str, ssh_key: str, machine_type: str,
           memory_gib: int, vcpu_count: int, gpu_count: int, gpu_model: str,
           tags: Dict[str, str]):
    disk = Disk(storage_class="STORAGE_CLASS_NETWORK",
                size_gib=100,
                id=generate_random_string(10))

    request = CreateVMBody(ssh_key_source="SSH_KEY_SOURCE_NONE",
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
        api = cudo_api.virtual_machines()
        vm = api.create_vm(cudo_api.project_id(), request)
        return vm.to_dict()['id']
    except ApiException as e:
        raise e


def terminate(instance_id: str):
    try:
        api = cudo_api.virtual_machines()
        api.terminate_vm(cudo_api.project_id(), instance_id)
    except ApiException as e:
        return None


def set_tags(instance_id: str, tags: Dict):
    try:
        api = cudo_api.virtual_machines()
        api.update_vm_metadata(
            cudo_api.project_id(), instance_id,
            UpdateVMMetadataBody(metadata=tags,
                                 merge=True))  # TODO (skypilot team) merge or overwrite?
    except ApiException as e:
        raise e


def get_instance(vm_id):
    try:
        api = cudo_api.virtual_machines()
        vm = api.get_vm(cudo_api.project_id(), vm_id)
        vm_dict = vm.to_dict()
        return vm_dict
    except ApiException as e:
        raise e


def list_instances():
    try:
        api = cudo_api.virtual_machines()
        vms = api.list_vms(cudo_api.project_id())
        instances = {}
        vms_dict = vms.to_dict()
        for vm in vms_dict['vms']:
            instance = {
                'status':
                    vm['short_state'
                      ],  # active_state, init_state, lcm_state, short_state
                'tags': vm['metadata'],
                'name': vm['id'],
                'ip': vm['public_ip_address'
                        ]  # public_ip_address, external_ip_address,
            }
            instances[vm['id']] = instance
        return instances
    except ApiException as e:
        raise e


def machine_types(gpu_model, mem_gib, vcpu_count, gpu_count):
    try:
        api = cudo_api.virtual_machines()
        types = api.list_vm_machine_types(mem_gib,
                                          vcpu_count,
                                          gpu=gpu_count,
                                          gpu_model=gpu_model)
        types_dict = types.to_dict()
        return types_dict
    except ApiException as e:
        raise e


def gpu_types():
    try:
        api = cudo_api.virtual_machines()
        types = api.list_vm_machine_types(4, 2)
        types_dict = types.to_dict()
        gpu_names = []
        for gpu in types_dict['gpu_models']:
            gpu_names.append(gpu['name'])
        return gpu_names
    except ApiException as e:
        raise e

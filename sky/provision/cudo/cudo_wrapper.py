"""Cudo Compute library wrapper for SkyPilot."""
import time
from typing import Dict

from sky import sky_logging
from sky.adaptors import cudo
import sky.provision.cudo.cudo_utils as utils

logger = sky_logging.init_logger(__name__)


def launch(name: str, data_center_id: str, ssh_key: str, machine_type: str,
           memory_gib: int, vcpu_count: int, gpu_count: int,
           tags: Dict[str, str], disk_size: int, node_type, net_name):
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
        nics=[cudo.cudo.CreateVMRequestNIC(network_id=net_name, assign_public_ip=node_type == 'head')],
        boot_disk=cudo.cudo.Disk(storage_class='STORAGE_CLASS_NETWORK', size_gib=disk_size),
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
            instance = {
                # active_state, init_state, lcm_state, short_state
                'status': vm['short_state'],
                'tags': vm['metadata'],
                'name': vm['id'],
                'external_ip': ex_ip,
                'internal_ip': in_ip
            }
            instances[vm['id']] = instance
        return instances
    except cudo.cudo.rest.ApiException as e:
        raise e


def vm_available(to_start_count, gpu_count, gpu_model_id, data_center_id, mem,
                 cpus):
    try:
        gpu_model_id = utils.skypilot_gpu_to_cudo_gpu(gpu_model_id)
        api = cudo.cudo.cudo_api.virtual_machines()
        types = api.list_vm_machine_types2()
        types_dict = types.to_dict()

        exists = False
        gpu_count_okay = False
        mem_size_okay = False
        cpu_count_okay = False

        for type in types_dict['machine_types']:
            if type['data_center_id'] == data_center_id and type['gpu_model_id'] == gpu_model_id:
                exists = True

                if (type['max_gpu_free'] > gpu_count and type['total_gpu_free'] > (
                        gpu_count * to_start_count)) or gpu_count == 0:
                    gpu_count_okay = True

                if type['max_memory_gib_free'] > mem and type['total_memory_gib_free'] > (mem * to_start_count):
                    mem_size_okay = True

                if type['max_vcpu_free'] > cpus and type['total_vcpu_free'] > (cpus * to_start_count):
                    cpu_count_okay = True

        if not exists:
            raise Exception('GPU model could not be found in data center')
        if not gpu_count_okay:
            raise Exception('Number of GPUs requested is too high')
        if not mem_size_okay:
            raise Exception('Memory size requested is too high')
        if not cpu_count_okay:
            raise Exception('Number of CPUs requested is too high')

        return True
    except cudo.cudo.rest.ApiException as e:
        raise e


def setup_network(region, network_id):
    api = cudo.cudo.cudo_api.networks()
    project_id = cudo.cudo.cudo_api.project_id_throwable()

    try:
        network = cudo.cudo.CreateNetworkBody(id=network_id,
                                              cidr_prefix='10.0.0.0/10',
                                              data_center_id=region)
        api.create_network(project_id, create_network_body=network)

    except cudo.cudo.rest.ApiException as e:
        raise e
    # Wait for network
    max_retries = 240
    retry_interval = 1
    wait = True
    retry_count = 0
    while wait:
        try:
            net = api.get_network(project_id, network_id)
            state = net.to_dict()['network']['short_state']
        except cudo.cudo.rest.ApiException as e:
            raise e

        if state == 'runn':
            wait = False
        else:
            time.sleep(retry_interval)
            retry_count += 1
            if retry_count > max_retries:
                net.delete_network(project_id, network_id)
                raise cudo.cudo.rest.ApiException('Network could not be created')


def delete_network(network_id):
    try:
        api = cudo.cudo.cudo_api.networks()
        project_id = cudo.cudo.cudo_api.project_id_throwable()
        api.delete_network(project_id, id=network_id, network_id=network_id)
    except cudo.cudo.rest.ApiException as e:
        raise e

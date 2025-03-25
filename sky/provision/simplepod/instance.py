"""SimplePod instance provisioning implementation."""
import copy
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sky import exceptions
from sky import status_lib
from sky.adaptors import simplepod
from sky.provision import common
from sky.provision.simplepod import simplepod_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import resources_utils

_TIMEOUT_SECONDS = 600

class SimplePodInstanceManager:
    def __init__(self) -> None:
        self.client = simplepod_utils.SimplePodClient()

    def wait_instances(self,
                      cluster_name: str,
                      instances_to_wait: List[str],
                      num_nodes: int,
                      task_id: str,
                      timeout: int = _TIMEOUT_SECONDS) -> None:
        """Wait for instances to be ready."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                ready = 0
                for instance_id in instances_to_wait:
                    instance = self.client.get_instance(instance_id)
                    # Update to match API status values
                    if instance['status'] in ['running', 'active']:
                        ready += 1
                if ready == num_nodes:
                    return
                time.sleep(5)
            except Exception as e:
                if hasattr(e, 'detail'):
                    raise exceptions.ResourcesUnavailableError(e.detail) from e
                raise exceptions.ResourcesUnavailableError(
                    'Failed to wait for instances to be ready.') from e
        raise exceptions.ResourcesUnavailableError(
            f'Timeout while waiting for instances after {timeout} seconds.')

    def run_instances(
            self,
            *,
            cluster_name: str,
            num_nodes: int,
            instance_type: str,
            task_id: str,
            region: Optional[str] = None,
            public_key: Optional[str] = None,
            template_id: Optional[int] = None,
            env_variables: Optional[List[Dict[str, str]]] = None,
    ) -> List['common.InstanceInfo']:
        """Launch instances."""
        request_data = {
            'gpuCount': num_nodes,
            'instanceMarket': f'/instances/market/{instance_type}',
        }

        if template_id:
            request_data['instanceTemplate'] = f'/instances/templates/{template_id}'
        if env_variables:
            request_data['envVariables'] = env_variables

        instance_ids = self.client.create_instance(**request_data)
        self.wait_instances(cluster_name, instance_ids, num_nodes, task_id)

        launched = []
        for instance_id in instance_ids:
            instance = self.client.get_instance(instance_id)
            launched.append(
                common.InstanceInfo(
                    instance_id=instance_id,
                    instance_name=instance.get('name', ''),
                    internal_ip=instance.get('private_ip', ''),
                    external_ip=instance.get('public_ip', ''),
                    tags={
                        'ray-cluster-name': cluster_name,
                        'skypilot-task-id': task_id,
                    },
                ))
        return launched

    def reboot_instances(self, instance_ids: List[str]) -> None:
        """Reboot specified instances."""
        try:
            self.client.reboot_instances(instance_ids)
        except Exception as e:
            if hasattr(e, 'detail'):
                raise exceptions.ResourcesUnavailableError(e.detail) from e
            raise exceptions.ResourcesUnavailableError(
                'Failed to reboot instances.') from e

def query_instances(
    cluster_name_tag: str) -> List[common.InstanceInfo]:
    """Query the instances in the cluster."""
    client = simplepod_utils.SimplePodClient()
    instances = client.list_instances()
    instance_infos = []
    for instance in instances:
        if instance['name'] == cluster_name_tag:
            instance_infos.append(
                common.InstanceInfo(
                    instance_id=instance['id'],
                    instance_name=instance['name'],
                    internal_ip=instance['private_ip'],
                    external_ip=instance['public_ip'],
                    tags={
                        'ray-cluster-name': instance['name'],
                    },
                ))
    return instance_infos

def stop_instances(cluster_name_tag: str) -> None:
    """Stop all instances in the cluster."""
    client = simplepod_utils.SimplePodClient()
    instances = client.list_instances()
    for instance in instances:
        if instance['name'] == cluster_name_tag:
            client.stop_instance(instance['id'])

def terminate_instances(cluster_name_tag: str) -> None:
    """Terminate all instances in the cluster."""
    client = simplepod_utils.SimplePodClient()
    instances = client.list_instances()
    for instance in instances:
        if instance['name'] == cluster_name_tag:
            client.delete_instance(instance['id'])

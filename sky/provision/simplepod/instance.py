"""SimplePod instance provisioning implementation."""

import os
import time
from typing import Any, Dict, List, Optional
from venv import logger

from sky import exceptions, sky_logging
from sky.authentication import _SSH_KEY_PATH_PREFIX
from sky.provision import common
from sky.provision.simplepod import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

_TIMEOUT_SECONDS = 600


client = utils.SimplePodClient()


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    client = utils.SimplePodClient()

    running_instances = {
        instance['id']: instance
        for instance in client.list_instances()
        if instance['name'].startswith(f'{cluster_name_on_cloud}_') and instance['status'] in ['running']
    }

    head_instance_id = next(
        (inst_id for inst_id, inst in running_instances.items() if inst['name'].endswith('_head')),
        None
    )

    to_start_count = max(0, config.count - len(running_instances))
    if to_start_count == 0:
        if head_instance_id is None:
            raise RuntimeError(f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has {len(running_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='simplepod',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[]
        )

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        instance_name = f'{cluster_name_on_cloud}-{node_type}'

        try:
            instance_id = client.create_instance(
                instance_type=config.node_config['InstanceType'],
                name=instance_name,
                ssh_key=config.node_config.get('SshKeyName', ''),
                env_variables=config.node_config.get('UserData', None),
            )
            created_instance_ids.append(instance_id)

            if head_instance_id is None:
                head_instance_id = instance_id

            logger.info(f'Launched instance {instance_id}')

        except Exception as e:
            logger.error(f'Failed to launch instance: {str(e)}')
            for inst_id in created_instance_ids:
                try:
                    client.delete_instance(inst_id)
                except Exception as delete_error:
                    logger.warning(f'Failed to delete instance {inst_id}: {delete_error}')
            raise

    assert head_instance_id is not None, 'head_instance_id should not be None'

    def are_instances_ready(instance_ids: List[str]) -> bool:
        instances = [client.get_instance(inst_id) for inst_id in instance_ids]
        return all(inst and inst.get('status') in ['running', 'active'] for inst in instances)

    for _ in range(30):  # 5 minut timeout
        if are_instances_ready(created_instance_ids):
            break
        time.sleep(10)
    else:
        raise TimeoutError('Timeout waiting for instances to be ready')

    return common.ProvisionRecord(
        provider_name='simplepod',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids
    )

def reboot_instance(self, instance_ids: List[str]) -> None:
        """Reboot specified instances."""
        try:
            client.reboot_instances(instance_ids)
        except Exception as e:
            if hasattr(e, 'detail'):
                raise exceptions.ResourcesUnavailableError(e.detail) from e
            raise exceptions.ResourcesUnavailableError(
                'Failed to reboot instances.') from e


def start_instances(self, instance_ids: List[str]) -> None:
        """Start specified instances."""
        try:
            for instance_id in instance_ids:
                client.create_instance(instance_id)
        except Exception as e:
            if hasattr(e, 'detail'):
                raise exceptions.ResourcesUnavailableError(e.detail) from e
            raise exceptions.ResourcesUnavailableError(
                'Failed to start instances.') from e

def get_instance_status(self, instance_id: str) -> str:
        """Get the status of a specific instance."""
        try:
            instance = client.get_instance(instance_id)
            return instance.get('status', 'unknown')
        except Exception as e:
            if hasattr(e, 'detail'):
                raise exceptions.ResourcesUnavailableError(e.detail) from e
            raise exceptions.ResourcesUnavailableError(
                'Failed to get instance status.') from e


def terminate_instances(self, instance_ids: List[str]) -> None:
    """Terminate specified instances."""
    try:
        for instance_id in instance_ids:
            client.delete_instance(instance_id)
    except Exception as e:
        if hasattr(e, 'detail'):
            raise exceptions.ResourcesUnavailableError(e.detail) from e
        raise exceptions.ResourcesUnavailableError(
            'Failed to terminate instances.') from e


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """Query instances.

    Returns a dictionary of instance IDs and status.

    A None status means the instance is marked as "terminated"
    or "terminating".
    """
    client = utils.SimplePodClient()
    instances = client.list_instances()

    status_map = {
        'running': status_lib.ClusterStatus.UP,
        'terminated': None,
    }

    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for instance in instances:
        if not instance['name'].startswith(f'{cluster_name_on_cloud}-'):
            continue
        status = status_map.get(instance['status'], None)
        if non_terminated_only and status is None:
            continue
        statuses[instance['id']] = status

    return statuses

def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    logger.debug(f'Skip opening ports {ports} for SimplePod instances, as all '
                 'ports are open by default.')
    del cluster_name_on_cloud, provider_config, ports


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, ports, provider_config  # Unused.

def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    cluster_name_on_cloud = cluster_name_on_cloud.replace('-', '_')
    running_instances = _filter_instances(cluster_name_on_cloud, ['running'])
    print(f"[DEBUG] Running instances: {running_instances.items()}")
    head_instance_id = None
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['rig']['ip'],
                external_ip=instance_info['rig']['ip'],
                ssh_port=instance_info['ports']['direct'][0]['destPort'][1],
                tags={}
            )
        ]
        if instance_info['name'].endswith('_head'):
            head_instance_id = instance_id
            print('[DEBUG]HEAD instannce is!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!:', head_instance_id)
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='simplepod',
        provider_config=provider_config,
    )

def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = True) -> Dict[str, Any]:
    instances = client.list_instances()
    print(f"[DEBUG] Wszystkie instancje: {instances}")

    filtered_instances = {}
    for instance in instances:
        name = instance['name']
        status = instance['status']

        print(f"[DEBUG] Sprawdzam instancję: {name}, status: {status}")

        if status_filters is not None and status not in [s.lower() for s in status_filters]:
            print(f"[DEBUG] Pomijam ze względu na status: {status}")
            continue

        if not name.startswith(cluster_name_on_cloud):
            print(f"[DEBUG] Pomijam ze względu na nazwę (nie zaczyna się od {cluster_name_on_cloud})")
            continue

        if head_only and 'head' not in name:
            print(f"[DEBUG] Pomijam, bo nie head: {name}")
            continue

        filtered_instances[instance['id']] = instance

    print(f"[DEBUG] Zwracam przefiltrowane instancje: {filtered_instances}")
    return filtered_instances

def save_instance_ssh_key(instance_info: Dict[str, Any], user_hash: str) -> str:

    # Check if the instance_info has the expected structure
    ssh_key = instance_info.get('hashId')
    if not ssh_key:
        raise ValueError("Brak klucza 'hashId' w danych instancji.")

    # Check if the user_hash is valid
    ssh_key_dir = os.path.expanduser(_SSH_KEY_PATH_PREFIX.format(user_hash=user_hash))
    os.makedirs(ssh_key_dir, exist_ok=True, mode=0o700)
    ssh_key_path = os.path.join(ssh_key_dir, 'sky-key')

    # Save the SSH key to the specified path
    with open(ssh_key_path, 'w', encoding='utf-8') as key_file:
        key_file.write(ssh_key)
        os.chmod(ssh_key_path, 0o600)  # Set permissions to read/write for the user only

    return ssh_key_path
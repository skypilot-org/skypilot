"""SimplePod instance provisioning implementation."""
import copy
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple
from venv import logger

from sky import exceptions
from sky.provision import common, wait_instances
from sky.provision.aws.instance import _filter_instances
from sky.provision.nebius.instance import _wait_until_no_pending
from sky.provision.simplepod import utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import resources_utils
from sky.utils import status_lib  # Changed from sky import status_lib

_TIMEOUT_SECONDS = 600


client = utils.SimplePodClient()

def wait_instance(self,
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
                    instance = client.get_instance(instance_id)
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

def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    client = utils.SimplePodClient()

    running_instances = {
        instance['id']: instance
        for instance in client.list_instances()
        if instance['name'].startswith(f'{cluster_name_on_cloud}-') and instance['status'] in ['running', 'active']
    }

    head_instance_id = next(
        (inst_id for inst_id, inst in running_instances.items() if inst['name'].endswith('-head')),
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
        'stopped': status_lib.ClusterStatus.STOPPED,
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
    logger.debug(f'Skip opening ports {ports} for Nebius instances, as all '
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
    _wait_until_no_pending(region, cluster_name_on_cloud)
    running_instances = _filter_instances(region, cluster_name_on_cloud,
                                          ['RUNNING'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['internal_ip'],
                external_ip=instance_info['external_ip'],
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id
    assert head_instance_id is not None
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='nebius',
        provider_config=provider_config,
    )
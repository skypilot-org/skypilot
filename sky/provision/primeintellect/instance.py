"""Primeintellect instance provisioning."""
import time
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.provision import common
from sky.provision.primeintellect import utils
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import ux_utils

# The maximum number of times to poll for the status of an operation.
POLL_INTERVAL = 5
MAX_POLLS = 60 // POLL_INTERVAL
# Stopping instances can take several minutes, so we increase the timeout
MAX_POLLS_FOR_UP_OR_STOP = MAX_POLLS * 16

# status filters
# PROVISIONING, PENDING, ACTIVE, STOPPED, ERROR, DELETING, TERMINATED

logger = sky_logging.init_logger(__name__)


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:
    client = utils.PrimeintellectAPIClient()
    instances = client.list_instances()
    # TODO: verify names are we using it?
    possible_names = [
        f'{cluster_name_on_cloud}-head',
        f'{cluster_name_on_cloud}-worker',
    ]

    filtered_instances = {}
    for instance in instances:
        instance_id = instance['id']
        if status_filters is not None and instance[
                'status'] not in status_filters:
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_instance_info(instance_id: str) -> Dict[str, Any]:
    client = utils.PrimeintellectAPIClient()
    return client.get_instance_details(instance_id)


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""

    pending_status = [
        'PROVISIONING',
        'PENDING',
    ]
    newly_started_instances = _filter_instances(cluster_name_on_cloud,
                                                pending_status)
    client = utils.PrimeintellectAPIClient()

    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        instance_statuses = [
            instance['status'] for instance in instances.values()
        ]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(POLL_INTERVAL)

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=pending_status)
    if len(exist_instances) > config.count:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')

    exist_instances = _filter_instances(cluster_name_on_cloud,
                                        status_filters=['ACTIVE'])
    head_instance_id = _get_head_instance_id(exist_instances)
    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            head_instance_id = list(exist_instances.keys())[0]
            # TODO: implement rename pod
            # client.rename(
            #     instance_id=head_instance_id,
            #     name=f'{cluster_name_on_cloud}-head',
            # )
        assert head_instance_id is not None, (
            'head_instance_id should not be None')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='primeintellect',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance_id,
            resumed_instance_ids=list(newly_started_instances.keys()),
            created_instance_ids=[],
        )

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            params = {
                "name": f'{cluster_name_on_cloud}-{node_type}',
                "instance_type": config.node_config['InstanceType'],
                "region": region,
                "disk_size": 120,
            }
            response = client.launch(**params)
            instance_id = response['id']
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise e
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id

    # Wait for instances to be ready.
    for _ in range(MAX_POLLS_FOR_UP_OR_STOP):
        instances = _filter_instances(cluster_name_on_cloud, ['ACTIVE'])
        logger.info('Waiting for instances to be ready: '
                    f'({len(instances)}/{config.count}).')
        if len(instances) == config.count:
            break

        time.sleep(POLL_INTERVAL)
    else:
        # Failed to launch config.count of instances after max retries
        msg = ('run_instances: Failed to create the'
               'instances due to capacity issue.')
        logger.warning(msg)
        raise RuntimeError(msg)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='primeintellect',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    client = utils.PrimeintellectAPIClient()
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            client.remove(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['ACTIVE'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    head_instance_ssh_user = None
    for instance_id in running_instances.keys():
        retry_count = 0
        max_retries = 6
        while running_instances[instance_id].get(
                'sshConnection') is None and retry_count < max_retries:
            print(
                f"SSH connection to {running_instances[instance_id].get('name')} is not ready, waiting 10 seconds... (attempt {retry_count + 1}/{max_retries})"
            )
            time.sleep(10)
            retry_count += 1
            running_instances[instance_id] = _get_instance_info(instance_id)

        if running_instances[instance_id].get('sshConnection') is not None:
            print("SSH connection is ready!")
        else:
            raise Exception(
                f"Failed to establish SSH connection after {max_retries} attempts"
            )

        assert running_instances[instance_id].get(
            'sshConnection'), "sshConnection cannot be null anymore"

        if ' -p ' in running_instances[instance_id]['sshConnection']:
            ssh_port = running_instances[instance_id]['sshConnection'].split(
                ' -p ')[1].strip()
        else:
            ssh_port = '22'
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip="NOT_SUPPORTED",
                external_ip=running_instances[instance_id]['ip'],
                ssh_port=int(ssh_port),
                tags={
                    "provider": running_instances[instance_id]['providerType']
                },
            )
        ]
        if running_instances[instance_id]['name'].endswith('-head'):
            head_instance_id = instance_id
            head_instance_ssh_user = running_instances[instance_id][
                'sshConnection'].split('@', 1)[0].strip()

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='primeintellect',
        provider_config=provider_config,
        ssh_user=head_instance_ssh_user,
    )


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)

    status_map = {
        'PENDING': status_lib.ClusterStatus.INIT,
        'ACTIVE': status_lib.ClusterStatus.UP,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'ERROR': status_lib.ClusterStatus.INIT,
        'DELETING': status_lib.ClusterStatus.INIT,
        'TERMINATED': status_lib.ClusterStatus.STOPPED,
    }
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst['status']]
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = status
    return statuses


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, ports, provider_config  # Unused.

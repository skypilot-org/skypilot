"""Lambda instance provisioning."""

import os
import time
from typing import Any, Dict, List, Optional

from sky import authentication as auth
from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.lambda_cloud.lambda_utils import LambdaCloudClient
from sky.provision.lambda_cloud.lambda_utils import LambdaCloudError
from sky.utils import common_utils
from sky.utils import ux_utils

POLL_INTERVAL = 5

logger = sky_logging.init_logger(__name__)
_lambda_client = None


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = LambdaCloudClient()
    return _lambda_client


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:
    lambda_client = _get_lambda_client()
    instances = lambda_client.list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head',
        f'{cluster_name_on_cloud}-worker',
    ]

    filtered_instances = {}
    for instance in instances:
        if status_filters is not None and instance[
                'status'] not in status_filters:
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance['id']] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for instance_id, instance in instances.items():
        if instance['name'].endswith('-head'):
            head_instance_id = instance_id
            break
    return head_instance_id


def _get_ssh_key_name(prefix: str = '') -> str:
    lambda_client = _get_lambda_client()
    public_key_path = os.path.expanduser(auth.PUBLIC_SSH_KEY_PATH)
    with open(public_key_path, 'r', encoding='utf-8') as f:
        public_key = f.read()
    name, exists = lambda_client.get_unique_ssh_key_name(prefix, public_key)
    if not exists:
        raise LambdaCloudError('SSH key not found')
    return name


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster"""
    lambda_client = _get_lambda_client()
    pending_status = ['booting']
    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)
    exist_instances = _filter_instances(cluster_name_on_cloud, ['active'])
    head_instance_id = _get_head_instance_id(exist_instances)

    to_start_count = config.count - len(exist_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(
            provider_name='lambda',
            cluster_name=cluster_name_on_cloud,
            region=region,
            zone=None,
            head_instance_id=head_instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[],
        )

    created_instance_ids = []
    ssh_key_name = _get_ssh_key_name()
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = lambda_client.create_instances(
                instance_type=config.node_config['InstanceType'],
                region=region,
                name=f'{cluster_name_on_cloud}-{node_type}',
                quantity=1,
                ssh_key_name=ssh_key_name,
            )[0]
        except Exception as e:
            logger.warning(f'run_instances error: {e}')
            raise
        logger.info(f'Launched instance {instance_id}')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id
        time.sleep(10)  # Avoid api rate limits

    while True:
        instances = _filter_instances(cluster_name_on_cloud, ['active'])
        ready_instance_cnt = 0
        for instance_id, instance in instances.items():
            if instance.get('status') == 'active':
                ready_instance_cnt += 1
        if ready_instance_cnt == config.count:
            break

        time.sleep(POLL_INTERVAL)

    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(
        provider_name='lambda',
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=[],
        created_instance_ids=created_instance_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state  # Unused.


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError(
        'stop_instances is not supported for Lambda Cloud')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config
    lambda_client = _get_lambda_client()
    instances = _filter_instances(cluster_name_on_cloud, None)
    # TODO (kmushegi): terminate all instances together since
    # remove_instances accepts multiple instance ids
    for instance_id, instance in instances.items():
        logger.debug(f'Terminating instance {instance_id}: {instance}')
        if worker_only and not instance['name'].endswith('-worker'):
            continue
        try:
            lambda_client.remove_instances(instance_id)
            logger.info(f'Terminated instance {instance_id}')
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {instance_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['active'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                # TODO (kmushegi): check if this is correct;
                # external ip is preferred
                internal_ip='',
                external_ip=instance_info['ip'],
                ssh_port=22,
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='lambda',
        provider_config=provider_config,
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
        'booting': status_lib.ClusterStatus.INIT,
        'active': status_lib.ClusterStatus.UP,
        'unhealthy': status_lib.ClusterStatus.INIT,
        'terminating': status_lib.ClusterStatus.STOPPED,
        'terminated': status_lib.ClusterStatus.STOPPED,
    }
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for instance_id, instance in instances.items():
        status = status_map.get(instance['status'])
        if non_terminated_only and status is None:
            continue
        statuses[instance_id] = status
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    # Do nothing because we assume the user has updated their firewall
    # rules to allow these ports
    del cluster_name_on_cloud, ports, provider_config  # Unused.


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, ports, provider_config  # Unused.


def query_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, provider_config  # Unused.
    return common.query_ports_passthrough(ports, head_ip)

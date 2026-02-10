"""Nebius instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.nebius import utils
from sky.utils import common_utils
from sky.utils import status_lib
from sky.utils import ux_utils

PENDING_STATUS = ['STARTING', 'DELETING', 'STOPPING']

MAX_RETRIES_TO_LAUNCH = 120  # Maximum number of retries

logger = sky_logging.init_logger(__name__)


def _filter_instances(region: str,
                      cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:
    project_id = utils.get_project_by_region(region)
    instances = utils.list_instances(project_id)
    filtered_instances = {}
    for instance_id, instance in instances.items():
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue

        if instance['name'] and instance['name'].startswith(
                f'{cluster_name_on_cloud}-'):
            if head_only and instance['name'].endswith('-worker'):
                continue
            else:
                filtered_instances[instance_id] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def _wait_until_no_pending(region: str, cluster_name_on_cloud: str) -> None:
    retry_count = 0
    while retry_count < MAX_RETRIES_TO_LAUNCH:
        instances = _filter_instances(region, cluster_name_on_cloud,
                                      PENDING_STATUS)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready '
                    f'(Attempt {retry_count + 1}/{MAX_RETRIES_TO_LAUNCH}).')
        time.sleep(utils.POLL_INTERVAL)
        retry_count += 1

    if retry_count == MAX_RETRIES_TO_LAUNCH:
        raise TimeoutError(f'Exceeded maximum retries '
                           f'({MAX_RETRIES_TO_LAUNCH * utils.POLL_INTERVAL}'
                           f' seconds) while waiting for instances'
                           f' to be ready.')


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name  # unused
    _wait_until_no_pending(region, cluster_name_on_cloud)
    running_instances = _filter_instances(region, cluster_name_on_cloud,
                                          ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)
    to_start_count = config.count - len(running_instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(running_instances)} nodes, but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(running_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(provider_name='nebius',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    created_instance_ids = []
    resumed_instance_ids = []
    stopped_instances = _filter_instances(region, cluster_name_on_cloud,
                                          ['STOPPED'])
    if config.resume_stopped_nodes and len(stopped_instances) > to_start_count:

        raise RuntimeError(
            'The number of running/stopped/stopping instances combined '
            f'({len(stopped_instances) + len(running_instances)}) in '
            f'cluster "{cluster_name_on_cloud}" is greater than the '
            f'number requested by the user ({config.count}). '
            'This is likely a resource leak. '
            'Use "sky down" to terminate the cluster.')

    for stopped_instance_id, _ in stopped_instances.items():
        if to_start_count > 0:
            try:
                utils.start(stopped_instance_id)
                resumed_instance_ids.append(stopped_instance_id)
                to_start_count -= 1
                if stopped_instances[stopped_instance_id]['name'].endswith(
                        '-head'):
                    head_instance_id = stopped_instance_id
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Start instance error: {e}')
                raise
            time.sleep(utils.POLL_INTERVAL)  # to avoid fake STOPPED status
            logger.info(f'Started instance {stopped_instance_id}.')

    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            platform, preset = config.node_config['InstanceType'].split('_')

            instance_id = utils.launch(
                cluster_name_on_cloud=cluster_name_on_cloud,
                node_type=node_type,
                platform=platform,
                preset=preset,
                region=region,
                image_family=config.node_config['ImageId'],
                disk_size=config.node_config['DiskSize'],
                user_data=config.node_config['UserData'],
                use_spot=config.node_config['use_spot'],
                associate_public_ip_address=(
                    not config.provider_config['use_internal_ips']),
                use_static_ip_address=config.provider_config.get(
                    'use_static_ip_address', False),
                filesystems=config.node_config.get('filesystems', []),
                network_tier=config.node_config.get('network_tier'))
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise
        logger.info(f'Launched instance {instance_id}.')
        created_instance_ids.append(instance_id)
        if head_instance_id is None:
            head_instance_id = instance_id
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='nebius',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=resumed_instance_ids,
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    _wait_until_no_pending(region, cluster_name_on_cloud)
    if state is not None:
        if state == status_lib.ClusterStatus.UP:
            stopped_instances = _filter_instances(region, cluster_name_on_cloud,
                                                  ['STOPPED'])
            if stopped_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} is in UP state, but '
                    f'{len(stopped_instances)} instances are stopped.')
        if state == status_lib.ClusterStatus.STOPPED:
            running_instances = _filter_instances(region, cluster_name_on_cloud,
                                                  ['RUNNIG'])

            if running_instances:
                raise RuntimeError(
                    f'Cluster {cluster_name_on_cloud} is in STOPPED state, but '
                    f'{len(running_instances)} instances are running.')


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    assert provider_config is not None
    exist_instances = _filter_instances(provider_config['region'],
                                        cluster_name_on_cloud, ['RUNNING'])
    for instance in exist_instances:
        if worker_only and instance.endswith('-head'):
            continue
        utils.stop(instance)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""

    assert provider_config is not None
    instances = _filter_instances(provider_config['region'],
                                  cluster_name_on_cloud,
                                  status_filters=None)
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            utils.remove(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e
    utils.delete_cluster(cluster_name_on_cloud, provider_config['region'])


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


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, retry_if_missing  # unused
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(provider_config['region'],
                                  cluster_name_on_cloud, None)

    status_map = {
        'STARTING': status_lib.ClusterStatus.INIT,
        'RUNNING': status_lib.ClusterStatus.UP,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'STOPPING': status_lib.ClusterStatus.STOPPED,
        'DELETING': status_lib.ClusterStatus.STOPPED,
    }
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for inst_id, inst in instances.items():
        status = status_map[inst['status']]
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = (status, None)
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

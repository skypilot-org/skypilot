"""Nebius instance provisioning."""
import os
import time
from time import sleep
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.nebius import utils
from sky.provision.nebius.utils import delete_cluster, get_iam_token_project_id, POLL_INTERVAL, get_project_by_region
from sky.utils import common_utils
from sky.utils import ux_utils

from nebius.sdk import SDK

PENDING_STATUS = ['STARTING', 'DELETING', 'STOPPING']

params = get_iam_token_project_id()
NEBIUS_IAM_TOKEN = params['iam_token']
NB_TENANT_ID = params['tenant_id']

sdk = SDK(credentials=NEBIUS_IAM_TOKEN)

QUERY_PORTS_TIMEOUT_SECONDS = 30

logger = sky_logging.init_logger(__name__)


def _filter_instances(project_id: str,
                      cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:
    instances = utils.list_instances(project_id)
    possible_names = [f'{cluster_name_on_cloud}-head']
    if not head_only:
        possible_names.append(f'{cluster_name_on_cloud}-worker')
    filtered_instances = {}
    for instance_id, instance in instances.items():
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['name'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def _wait_all_pending(project_id: str, cluster_name_on_cloud: str) -> None:
    while True:
        instances = _filter_instances(project_id, cluster_name_on_cloud, PENDING_STATUS)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    project_id = get_project_by_region(region)
    _wait_all_pending(project_id, cluster_name_on_cloud)
    running_instances = _filter_instances(project_id, cluster_name_on_cloud, ['RUNNING'])
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
    stopped_instances = _filter_instances(project_id, cluster_name_on_cloud, ['STOPPED'])
    for stopped_instance in stopped_instances.keys():
        if to_start_count > 0:
            try:
                utils.start(stopped_instance)
                resumed_instance_ids.append(stopped_instance)
                to_start_count -= 1
                if stopped_instances[stopped_instance]['name'].endswith('-head'):
                    head_instance_id = stopped_instance
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Start instance error: {e}')
                raise
            sleep(POLL_INTERVAL)  # to avoid fake STOPPED status
            logger.info(f'Started instance {stopped_instance}.')

    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_id = utils.launch(
                name=f'{cluster_name_on_cloud}-{node_type}',
                instance_type=config.node_config['InstanceType'],
                region=region,
                disk_size=config.node_config['DiskSize'],
                user_data=config.node_config['UserData']
            )
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
    project_id = get_project_by_region(region)
    _wait_all_pending(project_id, cluster_name_on_cloud)


def stop_instances(
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None,
        worker_only: bool = False,
) -> None:
    project_id = get_project_by_region(provider_config['region'])
    exist_instances = _filter_instances(project_id, cluster_name_on_cloud, ['RUNNING'])
    for instance in exist_instances:
        utils.stop(instance)


def terminate_instances(
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None,
        worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    project_id = get_project_by_region(provider_config['region'])
    instances = _filter_instances(project_id, cluster_name_on_cloud, None)
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
    delete_cluster(cluster_name_on_cloud, project_id)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    project_id = get_project_by_region(region)
    _wait_all_pending(project_id, cluster_name_on_cloud)
    running_instances = _filter_instances(project_id, cluster_name_on_cloud, ['RUNNING'])
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

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='nebius',
        provider_config=provider_config,
    )


def query_instances(
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None,
        non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    project_id = get_project_by_region(provider_config['region'])
    instances = _filter_instances(project_id, cluster_name_on_cloud, None)

    status_map = {
        'STARTING': status_lib.ClusterStatus.INIT,
        'RUNNING': status_lib.ClusterStatus.UP,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'STOPPING': status_lib.ClusterStatus.STOPPED,
        'DELETING': status_lib.ClusterStatus.STOPPED,
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
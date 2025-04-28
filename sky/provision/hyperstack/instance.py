"""Hyperstack instance provisioning."""
import enum
import time
from typing import Any, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision.hyperstack import hyperstack_utils as utils
from sky.utils import common_utils
from sky.utils import ux_utils
from sky.utils.status_lib import ClusterStatus

POLL_INTERVAL = 5
TERMINATE_RETRY_INTERVAL = 10
TERMINATE_RETRY_COUNT = 18

logger = sky_logging.init_logger(__name__)


class HyperstackStatusGroup(enum.Enum):
    """Status groups enum for Hyperstack instances.
    Logically grouping statuses to make code that uses them
    more readable.
    """
    ACTIVE = 'ACTIVE'
    PENDING = 'PENDING'
    STOPPED = 'STOPPED'


class HyperstackStatus(enum.Enum):
    """Statuses enum for Hyperstack instances."""
    BUILD = ('BUILD', HyperstackStatusGroup.PENDING)
    CREATING = ('CREATING', HyperstackStatusGroup.PENDING)
    STARTING = ('STARTING', HyperstackStatusGroup.PENDING)
    REBOOTING = ('REBOOTING', HyperstackStatusGroup.PENDING)
    HARD_REBOOT = ('HARD_REBOOT', HyperstackStatusGroup.PENDING)
    STOPPING = ('STOPPING', HyperstackStatusGroup.PENDING)
    SHUTOFF = ('SHUTOFF', HyperstackStatusGroup.STOPPED)
    DELETING = ('DELETING', HyperstackStatusGroup.PENDING)
    ERROR = ('ERROR', HyperstackStatusGroup.PENDING)
    ACTIVE = ('ACTIVE', HyperstackStatusGroup.ACTIVE)

    @staticmethod
    def get_status(instance_status: str) -> 'HyperstackStatus':
        try:
            return HyperstackStatus[instance_status]
        except KeyError as e:
            logger.warning(f'get_status error: {e}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to parse status from Hyperstack: {instance_status}'
                )

    @staticmethod
    def get_cluster_status(instance_status: str) -> Optional['ClusterStatus']:
        hs = HyperstackStatus.get_status(instance_status)
        statuses = {
            HyperstackStatus.BUILD: ClusterStatus.INIT,
            HyperstackStatus.CREATING: ClusterStatus.INIT,
            HyperstackStatus.STARTING: ClusterStatus.INIT,
            HyperstackStatus.REBOOTING: ClusterStatus.INIT,
            HyperstackStatus.HARD_REBOOT: ClusterStatus.INIT,
            HyperstackStatus.STOPPING: ClusterStatus.STOPPED,
            HyperstackStatus.SHUTOFF: ClusterStatus.STOPPED,
            HyperstackStatus.DELETING: None,
            HyperstackStatus.ERROR: None,
            HyperstackStatus.ACTIVE: ClusterStatus.UP,
        }
        return statuses[hs]

    def get_group(self) -> HyperstackStatusGroup:
        return self.value[1]

    def is_active(self) -> bool:
        return self == HyperstackStatus.ACTIVE


def _filter_instances(
        cluster_name_on_cloud: str,
        status_group_filter: Optional[List[HyperstackStatusGroup]] = None,
        include_instances: Optional[List[str]] = None) -> Dict[str, Any]:

    instances = utils.HyperstackClient().list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head', f'{cluster_name_on_cloud}-worker'
    ]

    filtered_instances = {}
    for instance in instances:
        instance_status = instance['status']
        status = HyperstackStatus.get_status(instance_status)
        if ((status_group_filter is not None) and
                status.get_group() not in status_group_filter):
            continue
        if (include_instances is not None and
                instance['id'] not in include_instances):
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance['id']] = instance
    return filtered_instances


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
    while True:
        instances = _filter_instances(cluster_name_on_cloud,
                                      [HyperstackStatusGroup.PENDING])
        if len(instances) > config.count:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} already has '
                f'{len(instances)} nodes, but {config.count} are '
                'required. Please try terminate the cluster and retry.')
        if not instances:
            break
        instance_statuses = [
            instance['status'] for instance in instances.values()
        ]
        logger.info(f'Waiting for {len(instances)} instances to be ready: '
                    f'{instance_statuses}')
        time.sleep(POLL_INTERVAL)
    stopped_instances = _filter_instances(cluster_name_on_cloud,
                                          [HyperstackStatusGroup.STOPPED])
    active_instances = _filter_instances(cluster_name_on_cloud,
                                         [HyperstackStatusGroup.ACTIVE])
    exist_instances = {**stopped_instances, **active_instances}
    head_instance_id = _get_head_instance_id(exist_instances)

    to_add_count = config.count - len(exist_instances)
    if to_add_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are '
            'required. Please try terminate the cluster and retry.')
    if to_add_count == 0 and len(stopped_instances) == 0:
        if head_instance_id is None:
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(exist_instances)} nodes, no need to start more.')
        return common.ProvisionRecord(provider_name='hyperstack',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    created_instance_ids = []
    for instance_id, instance in stopped_instances.items():
        try:
            utils.HyperstackClient().start(instance_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'run_instances error starting stopped instance: {e}')
            raise
        logger.info(f'Started stopped instance {instance_id}.')
        created_instance_ids.append(instance_id)

    to_create_count = to_add_count - len(stopped_instances)
    for _ in range(to_create_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_ids = utils.HyperstackClient().create_instance(
                name=f'{cluster_name_on_cloud}-{node_type}',
                instance_type=config.node_config['InstanceType'],
                ssh_pub_key=config.node_config['AuthorizedKey'],
                region=region,
                ports=config.ports_to_open_on_launch,
                count=1)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error creating instance: {e}')
            raise
        logger.info(f'Launched instance {instance_ids[0]}.')
        created_instance_ids.append(instance_ids[0])
        if head_instance_id is None:
            head_instance_id = instance_ids[0]

    # Wait for instances to be ready.
    while True:
        instances = _filter_instances(cluster_name_on_cloud)
        if len(instances) < config.count:
            all_instances = _filter_instances(
                cluster_name_on_cloud, include_instances=created_instance_ids)
            all_statuses = [
                instance['status'] for instance in all_instances.values()
            ]
            failed_instance_cnt = config.count - len(instances)
            logger.error(f'Failed to create {failed_instance_cnt} '
                         f'instances for cluster {cluster_name_on_cloud}, '
                         f'with statuses: {all_statuses}')
            raise RuntimeError(
                f'Failed to create {failed_instance_cnt} instances, '
                f'with statuses: {all_statuses}')

        ready_instances = []
        pending_instances = []
        for instance in instances.values():
            status = HyperstackStatus[instance['status']]
            if (status.is_active() and instance['vm_state'] == 'active' and
                    instance['floating_ip_status'] == 'ATTACHED'):
                ready_instances.append(instance)
            else:
                pending_instances.append(instance)
        ready_instance_cnt = len(ready_instances)
        pending_statuses = [
            f'{instance["status"]} (vm_state: {instance["vm_state"]}, '
            f'floating_ip: {instance["floating_ip_status"]})'
            for instance in pending_instances
        ]
        logger.info('Waiting for instances to be ready: '
                    f'({ready_instance_cnt}/{config.count}).\n'
                    f'  Pending instance statuses: {pending_statuses}')
        if ready_instance_cnt == config.count:
            break

        time.sleep(POLL_INTERVAL)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='hyperstack',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop running instances."""
    del worker_only
    instances_map = query_instances(cluster_name_on_cloud, provider_config)
    for inst_id, _ in instances_map.items():
        utils.HyperstackClient().stop(inst_id)


def _terminate_instances(
    cluster_name_on_cloud: str,
    worker_only: bool = False,
    retry_count: int = 1,
) -> None:
    """See sky/provision/__init__.py"""
    instances = _filter_instances(cluster_name_on_cloud)
    exceptions = []
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            utils.HyperstackClient().delete(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Terminating instance {inst_id} failed: {e}.')
            exceptions.append(e)

    for e in exceptions:
        if "Please wait until the creation process is finished" not in str(e):
            # Reason: RuntimeError: Failed to terminate instance 238144: sky.provision.hyperstack.hyperstack_utils.HyperstackAPIError:
            # VM t-cancel-launch-a-d3-d2-cca4b597-head is currently being created and cannot be deleted yet.
            # Please wait until the creation process is finished before attempting deletion..
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e

    if len(exceptions) > 0:
        if retry_count <= TERMINATE_RETRY_COUNT:
            logger.debug(
                f'Waiting to retry ({retry_count}) '
                f'to terminate instance {inst_id}.'
            )
            time.sleep(TERMINATE_RETRY_INTERVAL)
            _terminate_instances(
                cluster_name_on_cloud=cluster_name_on_cloud,
                worker_only=worker_only,
                retry_count=retry_count+1,
            )
        else:
            logger.debug(f'Failed to terminate instance {inst_id}')
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    _terminate_instances(
        cluster_name_on_cloud=cluster_name_on_cloud,
        worker_only=worker_only,
    )


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud,
                                          [HyperstackStatusGroup.ACTIVE])
    instances: Dict[str, List[common.InstanceInfo]] = {}

    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instance_id = instance_info['id']
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['fixed_ip'],
                external_ip=instance_info['floating_ip'],
                ssh_port=22,
                tags={},
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    ci = common.ClusterInfo(instances=instances,
                            head_instance_id=head_instance_id,
                            custom_ray_options={'use_external_ip': True},
                            provider_name='hyperstack',
                            provider_config=provider_config)
    return ci


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud)
    statuses: Dict[str, Optional[ClusterStatus]] = {}
    for inst_id, inst in instances.items():
        status = HyperstackStatus.get_cluster_status(inst['status'])
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = status
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    instances_map = query_instances(cluster_name_on_cloud, provider_config)
    int_ports = [int(p) for p in ports]
    for inst_id, _ in instances_map.items():
        utils.HyperstackClient().open_ports(inst_id, int_ports)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    # Hyperstack will automatically cleanup network security groups when cleanup
    # VM. So we don't need to do anything here.
    del cluster_name_on_cloud, ports, provider_config  # Unused.

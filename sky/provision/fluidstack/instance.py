"""FluidStack instance provisioning."""
import time
from typing import Any, Dict, List, Optional

from sky import authentication as auth
from sky import exceptions
from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.fluidstack import fluidstack_utils as utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

_GET_INTERNAL_IP_CMD = ('ip -4 -br addr show | grep UP | grep -Eo '
                        r'"(10\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)|'
                        r'172\.(1[6-9]|2[0-9]|3[0-1]))\.(25[0-5]|'
                        r'2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|'
                        r'2[0-4][0-9]|[01]?[0-9][0-9]?)"')
POLL_INTERVAL = 5

logger = sky_logging.init_logger(__name__)


def get_internal_ip(node_info: Dict[str, Any]) -> None:
    node_info['internal_ip'] = node_info['ip_address']
    runner = command_runner.SSHCommandRunner(
        node_info['ip_address'],
        ssh_user=node_info['capabilities']['default_user_name'],
        ssh_private_key=auth.PRIVATE_SSH_KEY_PATH)
    result = runner.run(_GET_INTERNAL_IP_CMD,
                        require_outputs=True,
                        stream_logs=False)

    if result[0] != 0:
        # Some DCs do not have internal IPs and can fail when getting
        # the IP. We set the `internal_ip` to the same as
        # external IP. It should be fine as the `ray cluster`
        # will also get and use that external IP in that case.
        logger.debug('Failed get obtain private IP from node')
    else:
        node_info['internal_ip'] = result[1].strip()


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]]) -> Dict[str, Any]:

    instances = utils.FluidstackClient().list_instances()
    possible_names = [
        f'{cluster_name_on_cloud}-head', f'{cluster_name_on_cloud}-worker'
    ]

    filtered_instances = {}
    for instance in instances:
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        if instance.get('hostname') in possible_names:
            filtered_instances[instance['id']] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    head_instance_id = None
    for inst_id, inst in instances.items():
        if inst['hostname'].endswith('-head'):
            head_instance_id = inst_id
            break
    return head_instance_id


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""

    pending_status = [
        'create',
        'requesting',
        'provisioning',
        'customizing',
        'starting',
        'stopping',
        'start',
        'stop',
        'reboot',
        'rebooting',
    ]

    while True:
        instances = _filter_instances(cluster_name_on_cloud, pending_status)
        if not instances:
            break
        logger.info(f'Waiting for {len(instances)} instances to be ready.')
        time.sleep(POLL_INTERVAL)
    exist_instances = _filter_instances(cluster_name_on_cloud, ['running'])
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
        return common.ProvisionRecord(provider_name='fluidstack',
                                      cluster_name=cluster_name_on_cloud,
                                      region=region,
                                      zone=None,
                                      head_instance_id=head_instance_id,
                                      resumed_instance_ids=[],
                                      created_instance_ids=[])

    created_instance_ids = []
    for _ in range(to_start_count):
        node_type = 'head' if head_instance_id is None else 'worker'
        try:
            instance_ids = utils.FluidstackClient().create_instance(
                hostname=f'{cluster_name_on_cloud}-{node_type}',
                instance_type=config.node_config['InstanceType'],
                ssh_pub_key=config.node_config['AuthorizedKey'],
                region=region)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'run_instances error: {e}')
            raise
        logger.info(f'Launched instance {instance_ids[0]}.')
        created_instance_ids.append(instance_ids[0])
        if head_instance_id is None:
            head_instance_id = instance_ids[0]

    # Wait for instances to be ready.
    while True:
        instances = _filter_instances(cluster_name_on_cloud, ['running'])
        ready_instance_cnt = len(instances)
        logger.info('Waiting for instances to be ready: '
                    f'({ready_instance_cnt}/{config.count}).')
        if ready_instance_cnt == config.count:
            break
        failed_instances = _filter_instances(
            cluster_name_on_cloud,
            ['timeout error', 'failed to create', 'out of stock'])
        if failed_instances:
            logger.error(f'Failed to create {len(failed_instances)}'
                         f'instances for cluster {cluster_name_on_cloud}')
            raise RuntimeError(
                f'Failed to create {len(failed_instances)} instances.')

        time.sleep(POLL_INTERVAL)
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='fluidstack',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


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
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Terminating instance {inst_id}: {inst}')
        if worker_only and inst['hostname'].endswith('-head'):
            continue
        try:
            utils.FluidstackClient().delete(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            if (isinstance(e, utils.FluidstackAPIError) and
                    'Machine is already terminated' in str(e)):
                logger.debug(f'Instance {inst_id} is already terminated.')
                continue
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to terminate instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region, provider_config  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['running'])
    instances: Dict[str, List[common.InstanceInfo]] = {}

    subprocess_utils.run_in_parallel(get_internal_ip,
                                     list(running_instances.values()))
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        instance_id = instance_info['id']
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['internal_ip'],
                external_ip=instance_info['ip_address'],
                ssh_port=instance_info['ssh_port'],
                tags={},
            )
        ]
        if instance_info['hostname'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(instances=instances,
                              head_instance_id=head_instance_id,
                              custom_ray_options={'use_external_ip': True})


def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)
    instances = _filter_instances(cluster_name_on_cloud, None)
    status_map = {
        'provisioning': status_lib.ClusterStatus.INIT,
        'requesting': status_lib.ClusterStatus.INIT,
        'create': status_lib.ClusterStatus.INIT,
        'customizing': status_lib.ClusterStatus.INIT,
        'stopping': status_lib.ClusterStatus.STOPPED,
        'stop': status_lib.ClusterStatus.STOPPED,
        'start': status_lib.ClusterStatus.INIT,
        'reboot': status_lib.ClusterStatus.STOPPED,
        'rebooting': status_lib.ClusterStatus.STOPPED,
        'stopped': status_lib.ClusterStatus.STOPPED,
        'starting': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        'failed to create': status_lib.ClusterStatus.INIT,
        'timeout error': status_lib.ClusterStatus.INIT,
        'out of stock': status_lib.ClusterStatus.INIT,
        'terminated': None,
    }
    statuses: Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for inst_id, inst in instances.items():
        if inst['status'] not in status_map:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ClusterStatusFetchingError(
                    f'Failed to parse status from Fluidstack: {inst["status"]}')
        status = status_map.get(inst['status'], None)
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = status
    return statuses


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, provider_config

"""Runcrate instance provisioning."""
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.runcrate import utils
from sky.utils import resources_utils
from sky.utils import status_lib

PROVIDER_NAME = 'runcrate'
_HEAD = 'head'
_WORKER = 'worker'
# Runcrate machines usually start in 2 to 6 minutes. The status endpoint is
# polled gently: the API allows 100 requests a minute per workspace.
_LAUNCH_TIMEOUT_SECONDS = 20 * 60
_POLL_INTERVAL_SECONDS = 10

_STATUS_MAP = {
    'starting': status_lib.ClusterStatus.INIT,
    'deploying': status_lib.ClusterStatus.INIT,
    'running': status_lib.ClusterStatus.UP,
    'error': None,
    'terminated': None,
}

logger = sky_logging.init_logger(__name__)


def _node_name(cluster_name_on_cloud: str, node_type: str) -> str:
    return f'{cluster_name_on_cloud}-{node_type}'


def _filter_instances(cluster_name_on_cloud: str) -> Dict[str, Dict[str, Any]]:
    """Starting and running instances of a cluster, by instance id.

    The API matches `search` as a substring, so names are compared exactly
    here: another cluster's name may start with this one.
    """
    names = {
        _node_name(cluster_name_on_cloud, _HEAD),
        _node_name(cluster_name_on_cloud, _WORKER),
    }
    return {
        instance['id']: instance
        for instance in utils.list_instances(cluster_name_on_cloud)
        if instance.get('name') in names
    }


def _is_head(instance: Dict[str, Any]) -> bool:
    return instance['name'].endswith(f'-{_HEAD}')


def _wait_until_running(instance_ids: List[str]) -> None:
    """Waits until every instance runs and has an IP address.

    An instance that fails to start is terminated and an error is raised, so
    that SkyPilot fails over to another region or instance type.
    """
    deadline = time.time() + _LAUNCH_TIMEOUT_SECONDS
    pending = set(instance_ids)
    while True:
        for instance_id in sorted(pending):
            state = utils.get_instance_status(instance_id)
            status = state.get('status')
            if status == 'running' and state.get('ip'):
                pending.discard(instance_id)
            elif status in ('error', 'terminated'):
                utils.delete_instance(instance_id)
                raise utils.RuncrateError(
                    f'Runcrate instance {instance_id} failed to start '
                    f'(status: {status}).')
        if not pending:
            return
        if time.time() > deadline:
            for instance_id in pending:
                utils.delete_instance(instance_id)
            raise utils.RuncrateError(
                f'Runcrate instances {sorted(pending)} did not start within '
                f'{_LAUNCH_TIMEOUT_SECONDS // 60} minutes.')
        time.sleep(_POLL_INTERVAL_SECONDS)


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """See sky/provision/__init__.py"""
    del cluster_name  # unused
    instances = _filter_instances(cluster_name_on_cloud)
    head_instance_id = next(
        (i for i, instance in instances.items() if _is_head(instance)), None)

    to_start_count = config.count - len(instances)
    if to_start_count < 0:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has {len(instances)} '
            f'nodes, but {config.count} are required.')

    created_instance_ids = []
    if to_start_count > 0:
        instance_type = config.node_config['InstanceType']
        offer = utils.find_offer(instance_type, region)
        if offer is None:
            raise utils.RuncrateError(
                f'No {instance_type} is available in {region} on Runcrate '
                'right now.')
        ssh_key_id = config.authentication_config['ssh_key_id']
        for _ in range(to_start_count):
            node_type = _HEAD if head_instance_id is None else _WORKER
            instance = utils.create_instance(
                _node_name(cluster_name_on_cloud, node_type), offer['id'],
                ssh_key_id)
            logger.debug(f'Created Runcrate instance {instance["id"]} '
                         f'({node_type}) from offer {offer["id"]}.')
            created_instance_ids.append(instance['id'])
            if head_instance_id is None:
                head_instance_id = instance['id']

    assert head_instance_id is not None, cluster_name_on_cloud
    _wait_until_running(list(instances) + created_instance_ids)

    return common.ProvisionRecord(provider_name=PROVIDER_NAME,
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=[],
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py

    Waiting is done in run_instances, which returns once every instance runs.
    """
    del region, cluster_name_on_cloud, state  # unused


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    raise NotImplementedError('Stopping instances is not supported on '
                              'Runcrate.')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    for instance_id, instance in _filter_instances(
            cluster_name_on_cloud).items():
        if worker_only and _is_head(instance):
            continue
        logger.debug(f'Terminating Runcrate instance {instance_id}.')
        utils.delete_instance(instance_id)


def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> common.ClusterInfo:
    """See sky/provision/__init__.py"""
    del region  # unused
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance in _filter_instances(
            cluster_name_on_cloud).items():
        if instance.get('status') != 'running' or not instance.get('ip'):
            continue
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                # Runcrate instances have no private network: clusters are
                # single-node, so the head reaches itself on localhost.
                internal_ip='127.0.0.1',
                external_ip=instance['ip'],
                ssh_port=22,
                tags={},
                node_name=instance['name'],
            )
        ]
        if _is_head(instance):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name=PROVIDER_NAME,
        provider_config=provider_config,
        ssh_user='root',
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, provider_config, retry_if_missing  # unused
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for instance_id, instance in _filter_instances(
            cluster_name_on_cloud).items():
        status = _STATUS_MAP.get(instance.get('status', ''),
                                 status_lib.ClusterStatus.INIT)
        if non_terminated_only and status is None:
            continue
        statuses[instance_id] = (status, None)
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Opens TCP ports to anywhere in the API key's environment.

    Runcrate firewall rules belong to an environment and apply to all of its
    instances; running instances pick up a new rule within about a minute.
    """
    del provider_config  # unused
    already_open: Set[int] = set()
    for rule in utils.list_firewall_rules():
        if (rule.get('protocol') == 'tcp' and
                '0.0.0.0/0' in rule.get('sources', [])):
            already_open.update(
                range(rule['port_from'],
                      (rule.get('port_to') or rule['port_from']) + 1))
    to_open = resources_utils.port_ranges_to_set(ports) - already_open
    for port_range in resources_utils.port_set_to_ranges(to_open):
        start, _, end = port_range.partition('-')
        port_from = int(start)
        port_to = int(end) if end else port_from
        logger.debug(f'Opening TCP {port_range} for {cluster_name_on_cloud}.')
        utils.add_firewall_rule(port_from, port_to,
                                f'SkyPilot {cluster_name_on_cloud}'[:100])


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Keeps the firewall rules.

    Rules apply to the whole environment, so other clusters and instances in
    it may rely on them.
    """
    del cluster_name_on_cloud, ports, provider_config  # unused
    logger.debug('Keeping Runcrate firewall rules: they apply to the whole '
                 'environment.')

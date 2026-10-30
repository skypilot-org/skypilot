"""UpCloud instance provisioning."""
import re
import shlex
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.upcloud import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils

PROVIDER_NAME = 'upcloud'
POLL_INTERVAL = utils.POLL_INTERVAL
TIMEOUT = utils.TIMEOUT
# UpCloud rejects storage smaller than this.
MIN_DISK_SIZE_GB = 10
_MAX_PARALLELISM = 8
_RETRY_IF_MISSING_ATTEMPTS = 3

_HEAD = 'head'
_WORKER = 'worker'

logger = sky_logging.init_logger(__name__)


def _filter_instances(
        cluster_name_on_cloud: str,
        status_filters: Optional[List[str]],
        worker_only: bool = False,
        client: Optional[utils.UpCloudClient] = None
) -> Dict[str, Dict[str, Any]]:
    if client is None:
        client = utils.UpCloudClient()
    servers = client.list_servers(cluster_name=cluster_name_on_cloud)
    filtered: Dict[str, Dict[str, Any]] = {}
    filters = ([s.lower() for s in status_filters]
               if status_filters is not None else None)
    for server in servers:
        uuid = server.get('uuid')
        if not uuid:
            continue
        if worker_only and _role_from_server(server) == _HEAD:
            continue
        state = str(server.get('state', '')).lower()
        if filters is not None and state not in filters:
            continue
        filtered[str(uuid)] = server
    return filtered


def _role_from_server(server: Dict[str, Any]) -> str:
    return utils.get_labels(server).get(utils.LABEL_ROLE, _HEAD)


def _get_head_instance_id(
        instances: Dict[str, Dict[str, Any]]) -> Optional[str]:
    for uuid, server in instances.items():
        if _role_from_server(server) == _HEAD:
            return uuid
    return None


def _worker_index(server: Dict[str, Any]) -> Optional[int]:
    match = re.search(r'-worker-(\d+)$', str(server.get('hostname', '')))
    return int(match.group(1)) if match else None


def _disk_size(config: common.ProvisionConfig) -> int:
    size = config.node_config.get('DiskSize')
    try:
        if size is None:
            raise TypeError('DiskSize is not set')
        size_gb = int(size)
    except (TypeError, ValueError) as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Invalid UpCloud disk size: {size!r}') from e
    if size_gb < MIN_DISK_SIZE_GB:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'UpCloud disk size must be at least '
                             f'{MIN_DISK_SIZE_GB} GB, got {size_gb} GB.')
    return size_gb


def _disk_tier(config: common.ProvisionConfig) -> str:
    tier = config.node_config.get('DiskTier')
    if isinstance(tier, resources_utils.DiskTier):
        return utils.disk_tier_to_upcloud(tier)
    if isinstance(tier, str) and tier.lower() in ('hdd', 'standard', 'maxiops'):
        return tier.lower()
    return utils.disk_tier_to_upcloud(None)


def _image_id(config: common.ProvisionConfig,
              client: utils.UpCloudClient) -> str:
    image_id = config.node_config.get('ImageId')
    if image_id:
        return str(image_id)
    gpu = config.node_config.get('DefaultImage') == 'gpu'
    return utils.resolve_template_uuid(client, gpu=gpu)


def _plan(config: common.ProvisionConfig) -> str:
    instance_type = config.node_config.get('InstanceType')
    if not instance_type:
        raise RuntimeError('UpCloud node_config missing InstanceType')
    if config.node_config.get('UseSpot'):
        return utils.spot_plan_name(str(instance_type))
    return str(instance_type)


def _ensure_server_group(client: utils.UpCloudClient,
                         cluster_name_on_cloud: str) -> str:
    """Return a server-group UUID with anti-affinity for multi-node clusters."""
    existing = client.list_server_groups(cluster_name=cluster_name_on_cloud)
    for group in existing:
        uuid = group.get('uuid')
        if uuid:
            return str(uuid)
    title = f'skypilot-{cluster_name_on_cloud}'
    group = client.create_server_group(title, cluster_name_on_cloud)
    uuid = group.get('uuid')
    if not uuid:
        raise utils.UpCloudError(
            f'No UUID in create_server_group response: {group}')
    logger.info(f'Created UpCloud server group {uuid} for '
                f'{cluster_name_on_cloud}.')
    return str(uuid)


def _build_server_body(client: utils.UpCloudClient,
                       config: common.ProvisionConfig, region: str,
                       cluster_name_on_cloud: str, role: str, hostname: str,
                       server_group: Optional[str]) -> Dict[str, Any]:
    template_uuid = _image_id(config, client)
    disk_size = _disk_size(config)
    try:
        template_size = int(client.get_storage(template_uuid).get('size') or 0)
    except (TypeError, ValueError):
        template_size = 0
    if disk_size < template_size:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'UpCloud disk size {disk_size} GB is smaller than the image '
                f'{template_uuid} ({template_size} GB). Increase disk_size.')
    storage_devices: List[Dict[str, Any]] = [{
        'action': 'clone',
        'storage': template_uuid,
        'title': f'{hostname}-os',
        'size': disk_size,
        'tier': _disk_tier(config),
    }]
    network_uuid = config.provider_config.get('network_uuid')
    interfaces: List[Dict[str, Any]] = [{
        'index': 1,
        'type': 'public',
        'ip_addresses': {
            'ip_address': [{
                'family': 'IPv4'
            }]
        },
    }]
    if network_uuid:
        interfaces.append({
            'index': 2,
            'type': 'private',
            'network': network_uuid,
            'ip_addresses': {
                'ip_address': [{
                    'family': 'IPv4'
                }]
            },
        })
    body: Dict[str, Any] = {
        'hostname': hostname,
        'title': hostname,
        'zone': region,
        'plan': _plan(config),
        'metadata': 'yes',
        'firewall': 'on',
        'login_user': {
            'username': utils.DEFAULT_SSH_USER,
            'ssh_keys': {
                'ssh_key': [utils.read_public_key()]
            },
        },
        'labels': utils.labels_as_list(cluster_name_on_cloud, role,
                                       config.node_config.get('Labels')),
        'storage_devices': {
            'storage_device': storage_devices
        },
        'networking': {
            'interfaces': {
                'interface': interfaces
            }
        },
    }
    user_data = config.node_config.get('UserData')
    if user_data:
        body['user_data'] = str(user_data)
    if server_group:
        body['server_group'] = server_group
    return body


def _create_server(client: utils.UpCloudClient, body: Dict[str, Any]) -> str:
    logger.info(f'Creating UpCloud server {body["hostname"]} in '
                f'{body["zone"]} ({body["plan"]}).')
    server = client.create_server(body)
    uuid = server.get('uuid')
    if not uuid:
        raise utils.UpCloudError(f'No UUID in create response: {server}')
    return str(uuid)


def _apply_firewall(client: utils.UpCloudClient, uuid: str,
                    port_ranges: List[Tuple[int, int]]) -> None:
    client.replace_firewall_rules(uuid, utils.build_firewall_rules(port_ranges))


def _start_servers(client: utils.UpCloudClient, uuids: List[str]) -> None:
    if not uuids:
        return

    def _start(uuid: str) -> None:
        logger.info(f'Starting stopped UpCloud server {uuid}.')
        client.start_server(uuid)

    subprocess_utils.run_in_parallel(_start, uuids, _MAX_PARALLELISM)
    utils.wait_for_states(client, uuids,
                          [utils.UpCloudInstanceStatus.STARTED.value])


def _delete_servers(client: utils.UpCloudClient,
                    servers: Dict[str, Dict[str, Any]]) -> None:
    """Stops and deletes servers, keeping attached non-boot volumes."""

    def _delete(item: Tuple[str, Dict[str, Any]]) -> None:
        uuid, _ = item
        # Cancellation can leave a new server in maintenance, where UpCloud
        # rejects stop requests. Refresh and wait for an actionable state.
        server = utils.wait_for_state(client, uuid, [
            utils.UpCloudInstanceStatus.STARTED.value,
            utils.UpCloudInstanceStatus.STOPPED.value,
        ])
        state = str(server.get('state', '')).lower()
        if state != utils.UpCloudInstanceStatus.STOPPED.value:
            client.stop_server(uuid, stop_type='hard')
            utils.wait_for_state(client, uuid,
                                 [utils.UpCloudInstanceStatus.STOPPED.value])
        detailed = client.get_server(uuid)
        boot_storage_uuid = utils.get_boot_storage_uuid(detailed)
        for device in utils.get_storage_devices(detailed):
            if str(device.get('storage')) != boot_storage_uuid:
                logger.info(f'Detaching volume {device.get("storage")} from '
                            f'UpCloud server {uuid}.')
                client.detach_storage(uuid, str(device.get('address')))
        logger.info(f'Deleting UpCloud server {uuid}.')
        client.delete_server(uuid)

    errors: List[str] = []

    def _delete_and_collect(item: Tuple[str, Dict[str, Any]]) -> None:
        try:
            _delete(item)
        except Exception as e:  # pylint: disable=broad-except
            errors.append(f'{item[0]}: {common_utils.format_exception(e)}')

    subprocess_utils.run_in_parallel(_delete_and_collect, list(servers.items()),
                                     _MAX_PARALLELISM)
    if errors:
        raise utils.UpCloudError('Failed to delete UpCloud servers: ' +
                                 '; '.join(errors))


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name
    client = utils.UpCloudClient()
    start_time = time.time()
    while True:
        if time.time() - start_time > TIMEOUT:
            raise TimeoutError(
                f'Timed out waiting for pending UpCloud instances in '
                f'{cluster_name_on_cloud}')
        pending_instances = _filter_instances(
            cluster_name_on_cloud,
            [utils.UpCloudInstanceStatus.MAINTENANCE.value],
            client=client)
        if not pending_instances:
            break
        logger.info(f'Waiting for {len(pending_instances)} pending UpCloud '
                    'instance(s).')
        time.sleep(POLL_INTERVAL)

    exist_instances = _filter_instances(
        cluster_name_on_cloud,
        [
            utils.UpCloudInstanceStatus.STARTED.value,
            utils.UpCloudInstanceStatus.STOPPED.value,
        ],
        client=client,
    )
    if len(exist_instances) > config.count:
        raise RuntimeError(
            f'Cluster {cluster_name_on_cloud} already has '
            f'{len(exist_instances)} nodes, but {config.count} are required.')

    stopped = {
        uuid:
        srv for uuid, srv in exist_instances.items() if str(srv.get(
            'state', '')).lower() == utils.UpCloudInstanceStatus.STOPPED.value
    }
    resumed_ids: List[str] = []
    if stopped:
        if config.resume_stopped_nodes:
            resumed_ids = list(stopped)
            _start_servers(client, resumed_ids)
        else:
            logger.info(f'Terminating {len(stopped)} stopped UpCloud '
                        'server(s) as resume_stopped_nodes is False.')
            _delete_servers(client, stopped)

    running = _filter_instances(cluster_name_on_cloud,
                                [utils.UpCloudInstanceStatus.STARTED.value],
                                client=client)
    head_instance_id = _get_head_instance_id(running)
    to_start_count = config.count - len(running)
    created_ids: List[str] = []
    if to_start_count > 0:
        server_group: Optional[str] = None
        if config.count > 1:
            server_group = _ensure_server_group(client, cluster_name_on_cloud)
        roles: List[Tuple[str, str]] = []
        if head_instance_id is None:
            roles.append(
                (_HEAD, utils.sanitize_hostname(cluster_name_on_cloud, _HEAD)))
        used_indices: Set[int] = {
            idx for idx in (_worker_index(s) for s in running.values())
            if idx is not None
        }
        next_index = 1
        while len(roles) < to_start_count:
            while next_index in used_indices:
                next_index += 1
            roles.append((_WORKER,
                          utils.sanitize_hostname(cluster_name_on_cloud,
                                                  f'{_WORKER}-{next_index}')))
            used_indices.add(next_index)
        bodies = [
            _build_server_body(client, config, region, cluster_name_on_cloud,
                               role, hostname, server_group)
            for role, hostname in roles
        ]
        created_ids = subprocess_utils.run_in_parallel(
            lambda body: _create_server(client, body), bodies, _MAX_PARALLELISM)
        utils.wait_for_states(client, created_ids,
                              [utils.UpCloudInstanceStatus.STARTED.value])
        port_ranges = utils.parse_port_ranges(config.ports_to_open_on_launch or
                                              [])
        subprocess_utils.run_in_parallel(
            lambda uuid: _apply_firewall(client, uuid, port_ranges),
            created_ids, _MAX_PARALLELISM)
        if head_instance_id is None:
            head_instance_id = created_ids[0]

    running = _filter_instances(cluster_name_on_cloud,
                                [utils.UpCloudInstanceStatus.STARTED.value],
                                client=client)
    if len(running) < config.count:
        raise RuntimeError(
            f'Failed to bring cluster {cluster_name_on_cloud} to '
            f'{config.count} node(s); have {len(running)}.')
    assert head_instance_id is not None
    return common.ProvisionRecord(
        provider_name=PROVIDER_NAME,
        cluster_name=cluster_name_on_cloud,
        region=region,
        zone=None,
        head_instance_id=head_instance_id,
        resumed_instance_ids=resumed_ids,
        created_instance_ids=created_ids,
    )


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region
    if state is None:
        return
    if state == status_lib.ClusterStatus.UP:
        target = utils.UpCloudInstanceStatus.STARTED.value
    elif state == status_lib.ClusterStatus.STOPPED:
        target = utils.UpCloudInstanceStatus.STOPPED.value
    else:
        raise RuntimeError(f'Unsupported state: {state}')
    client = utils.UpCloudClient()
    instances = _filter_instances(cluster_name_on_cloud, None, client=client)
    if not instances:
        raise RuntimeError(
            f'No instances found for cluster {cluster_name_on_cloud}')
    utils.wait_for_states(client, list(instances), [target])


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    del provider_config
    client = utils.UpCloudClient()
    instances = _filter_instances(cluster_name_on_cloud,
                                  [utils.UpCloudInstanceStatus.STARTED.value],
                                  worker_only=worker_only,
                                  client=client)
    if not instances:
        return

    def _stop(uuid: str) -> None:
        logger.info(f'Stopping UpCloud server {uuid}.')
        client.stop_server(uuid)

    subprocess_utils.run_in_parallel(_stop, list(instances), _MAX_PARALLELISM)
    utils.wait_for_states(client, list(instances),
                          [utils.UpCloudInstanceStatus.STOPPED.value])


def _wait_cluster_servers_gone(client: utils.UpCloudClient,
                               cluster_name_on_cloud: str) -> None:
    deadline = time.time() + TIMEOUT
    while client.list_servers(cluster_name=cluster_name_on_cloud):
        if time.time() > deadline:
            raise TimeoutError('Timed out waiting for UpCloud servers of '
                               f'{cluster_name_on_cloud} to be deleted.')
        time.sleep(POLL_INTERVAL)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    worker_only: bool = False,
) -> None:
    """Deletes the cluster servers, their server group and network."""
    del provider_config
    client = utils.UpCloudClient()
    instances = _filter_instances(cluster_name_on_cloud,
                                  None,
                                  worker_only=worker_only,
                                  client=client)
    if instances:
        _delete_servers(client, instances)
    if worker_only:
        return
    _wait_cluster_servers_gone(client, cluster_name_on_cloud)
    for group in client.list_server_groups(cluster_name=cluster_name_on_cloud):
        group_uuid = group.get('uuid')
        if group_uuid:
            client.delete_server_group(str(group_uuid))
            logger.info(f'Deleted UpCloud server group {group_uuid}.')
    # Only networks created by bootstrap_instances carry the cluster label;
    # user-provided and volume networks are left in place.
    for network in client.list_networks(cluster_name=cluster_name_on_cloud):
        network_uuid = network.get('uuid')
        if network_uuid:
            client.delete_network(str(network_uuid))
            logger.info(f'Deleted UpCloud network {network_uuid}.')


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region
    client = utils.UpCloudClient()
    running = _filter_instances(cluster_name_on_cloud,
                                [utils.UpCloudInstanceStatus.STARTED.value],
                                client=client)
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = _get_head_instance_id(running)
    for uuid, server in running.items():
        detailed = client.get_server(uuid)
        external_ip = utils.get_public_ip(detailed)
        if not external_ip:
            raise RuntimeError(f'No public IP for UpCloud server {uuid}')
        internal_ip = utils.get_private_ip(detailed) or external_ip
        instances[uuid] = [
            common.InstanceInfo(
                instance_id=uuid,
                internal_ip=internal_ip,
                external_ip=external_ip,
                ssh_port=22,
                tags=utils.get_labels(server),
                node_name=str(server.get('hostname', uuid)),
            )
        ]
    custom_ray_options = None
    if head_instance_id is not None:
        head_ip = instances[head_instance_id][0].internal_ip
        # Select the source address of the private route on each node, rather
        # than letting Ray discover the public NIC via its default route.
        route_script = ('import socket; '
                        's = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); '
                        f's.connect(({head_ip!r}, 9)); '
                        'print(s.getsockname()[0]); s.close()')
        custom_ray_options = {
            'node-ip-address': '"$(python3 -c ' + shlex.quote(route_script) +
                               ')"'
        }
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name=PROVIDER_NAME,
        provider_config=provider_config,
        ssh_user=utils.DEFAULT_SSH_USER,
        custom_ray_options=custom_ray_options,
    )


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[dict] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    del cluster_name, provider_config
    instances = _filter_instances(cluster_name_on_cloud, None)
    attempts = 1
    while (not instances and retry_if_missing and
           attempts < _RETRY_IF_MISSING_ATTEMPTS):
        time.sleep(POLL_INTERVAL)
        instances = _filter_instances(cluster_name_on_cloud, None)
        attempts += 1
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for uuid, server in instances.items():
        try:
            raw = str(server.get('state', 'maintenance'))
            mapped = utils.UpCloudInstanceStatus.from_raw_status(
                raw).to_cluster_status()
        except utils.UpCloudError as e:
            logger.warning(f'Failed to parse status for {uuid}: {e}')
            mapped = status_lib.ClusterStatus.INIT
        if non_terminated_only and mapped is None:
            continue
        statuses[uuid] = (mapped, None)
    return statuses


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Opens ports by atomically replacing each server's firewall chain."""
    del provider_config
    client = utils.UpCloudClient()
    requested = utils.parse_port_ranges(ports)
    instances = _filter_instances(cluster_name_on_cloud, None, client=client)

    def _open(uuid: str) -> None:
        current = utils.opened_port_ranges(client.list_firewall_rules(uuid))
        port_ranges = current + [r for r in requested if r not in current]
        logger.info(f'Opening TCP ports {port_ranges} on UpCloud server '
                    f'{uuid}.')
        _apply_firewall(client, uuid, port_ranges)

    subprocess_utils.run_in_parallel(_open, list(instances), _MAX_PARALLELISM)


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Firewall rules belong to the servers and are deleted with them."""
    del cluster_name_on_cloud, ports, provider_config

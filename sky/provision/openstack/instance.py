"""OpenStack instance lifecycle implementation."""

from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.provision import common
from sky.provision.openstack import utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

_WAIT_TIMEOUT_SECONDS = 600
_WAIT_INTERVAL_SECONDS = 2


def _wait_for_server(connection: Any, server: Any, target: str) -> Any:
    if utils.server_status(server) == 'ERROR':
        raise RuntimeError(utils.format_server_fault(server))
    try:
        server = connection.compute.wait_for_server(
            server,
            status=target,
            failures=['ERROR'],
            interval=_WAIT_INTERVAL_SECONDS,
            wait=_WAIT_TIMEOUT_SECONDS)
    except Exception as exc:
        try:
            refreshed = connection.compute.get_server(
                utils.get_attr(server, 'id'))
        except Exception:  # pylint: disable=broad-except
            refreshed = server
        if utils.server_status(refreshed) == 'ERROR':
            raise RuntimeError(utils.format_server_fault(refreshed)) from exc
        raise RuntimeError(
            f'OpenStack server {utils.get_attr(server, "id")} did not reach '
            f'{target}: {exc}') from exc
    if utils.server_status(server) == 'ERROR':
        raise RuntimeError(utils.format_server_fault(server))
    return server


def _find_required_resource(resource: Any, kind: str, requested: str) -> Any:
    if resource is None:
        raise ValueError(f'OpenStack {kind} {requested!r} was not found.')
    return resource


def _port_for_server(connection: Any, server: Any, network_id: str) -> Any:
    ports = list(
        connection.network.ports(device_id=utils.get_attr(server, 'id'),
                                 network_id=network_id))
    if len(ports) != 1:
        raise RuntimeError(
            f'Expected exactly one OpenStack port for server '
            f'{utils.get_attr(server, "id")!r} on network {network_id!r}, '
            f'found {len(ports)}.')
    return ports[0]


def _ensure_floating_ip(connection: Any, server: Any, cluster_name: str,
                        node_config: Dict[str, Any]) -> Any:
    _, existing_external_ip = utils.server_ips(server)
    if existing_external_ip is not None:
        return None

    port = _port_for_server(connection, server, node_config['NetworkId'])
    managed_floating_ips = utils.managed_floating_ips(connection, cluster_name)
    for floating_ip in managed_floating_ips:
        if utils.get_attr(floating_ip, 'port_id') == utils.get_attr(port, 'id'):
            return None

    reusable_floating_ips = [
        floating_ip for floating_ip in managed_floating_ips
        if utils.get_attr(floating_ip, 'port_id') is None and
        utils.get_attr(floating_ip, 'floating_network_id') ==
        node_config['ExternalNetworkId']
    ]
    if reusable_floating_ips:
        floating_ip = min(reusable_floating_ips,
                          key=lambda item: str(utils.get_attr(item, 'id')))
    else:
        floating_ip = connection.network.create_ip(
            floating_network_id=node_config['ExternalNetworkId'],
            description=utils.floating_ip_description(cluster_name))
    try:
        connection.network.set_tags(
            floating_ip, utils.managed_resource_tags(cluster_name,
                                                     'floating-ip'))
        connection.network.update_ip(floating_ip,
                                     port_id=utils.get_attr(port, 'id'))
    except Exception:
        connection.network.delete_ip(floating_ip, ignore_missing=True)
        raise
    return floating_ip


def _cleanup_failed_launch(connection: Any,
                           config: common.ProvisionConfig,
                           server: Optional[Any] = None,
                           floating_ip: Optional[Any] = None) -> None:
    """Rolls back only resources known to have been created this attempt."""
    if floating_ip is not None:
        try:
            connection.network.delete_ip(floating_ip, ignore_missing=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(f'Failed to roll back OpenStack floating IP: {exc}')
    if server is not None:
        try:
            connection.compute.delete_server(server, ignore_missing=True)
            connection.compute.wait_for_delete(server,
                                               interval=_WAIT_INTERVAL_SECONDS,
                                               wait=_WAIT_TIMEOUT_SECONDS)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(f'Failed to roll back OpenStack server: {exc}')

    security_group_config = config.provider_config.get('_sky_security_group',
                                                       {})
    if security_group_config.get('created'):
        try:
            connection.network.delete_security_group(
                security_group_config['id'], ignore_missing=True)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to roll back OpenStack security group: {exc}')


def _record(region: str,
            cluster_name: str,
            server_id: str,
            created: Optional[List[str]] = None,
            resumed: Optional[List[str]] = None,
            zone: Optional[str] = None) -> common.ProvisionRecord:
    return common.ProvisionRecord(provider_name='openstack',
                                  region=region,
                                  zone=zone,
                                  cluster_name=cluster_name,
                                  head_instance_id=server_id,
                                  created_instance_ids=created or [],
                                  resumed_instance_ids=resumed or [])


def _reuse_instance(connection: Any, server: Any,
                    config: common.ProvisionConfig) -> Tuple[Any, bool]:
    status = utils.server_status(server)
    if status == 'ERROR':
        raise RuntimeError(utils.format_server_fault(server))
    if status == 'ACTIVE':
        return server, False
    if status in ('SHUTOFF', 'STOPPED'):
        if not config.resume_stopped_nodes:
            raise RuntimeError(
                'OpenStack cluster has a stopped instance. Set '
                '`resume_stopped_nodes` or run `sky start` to resume it.')
        connection.compute.start_server(server)
        return _wait_for_server(connection, server, 'ACTIVE'), True
    if status == 'STOPPING':
        server = _wait_for_server(connection, server, 'SHUTOFF')
        if not config.resume_stopped_nodes:
            raise RuntimeError('OpenStack cluster finished stopping; refusing '
                               'to create a duplicate instance.')
        connection.compute.start_server(server)
        return _wait_for_server(connection, server, 'ACTIVE'), True
    if status in ('DELETED', 'SOFT_DELETED', 'DELETING'):
        raise RuntimeError('OpenStack cluster instance is still being deleted; '
                           'retry after teardown completes.')
    return _wait_for_server(connection, server, 'ACTIVE'), False


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Creates or resumes the exact single head node for a cluster."""
    del cluster_name  # The cloud-safe name is the ownership identifier.
    if config.count != 1:
        raise ValueError('OpenStack MVP supports single-node clusters only.')

    connection = utils.get_connection(config.provider_config, region)
    existing = utils.list_cluster_instances(connection, cluster_name_on_cloud)
    if len(existing) > 1:
        raise RuntimeError(
            f'OpenStack cluster {cluster_name_on_cloud!r} has '
            f'{len(existing)} owned instances; expected exactly one.')
    if existing:
        server, resumed = _reuse_instance(connection, existing[0], config)
        if not config.provider_config.get('use_internal_ips', False):
            _ensure_floating_ip(connection, server, cluster_name_on_cloud,
                                config.node_config)
        server_id = utils.get_attr(server, 'id')
        zone = config.node_config.get('AvailabilityZone')
        return _record(region,
                       cluster_name_on_cloud,
                       server_id,
                       resumed=[server_id] if resumed else [],
                       zone=zone)

    server = None
    floating_ip = None
    try:
        image_name = config.node_config['ImageId']
        image = _find_required_resource(
            connection.image.find_image(image_name, ignore_missing=True),
            'image', image_name)
        flavor_name = config.node_config['InstanceType']
        flavor = _find_required_resource(
            connection.compute.find_flavor(flavor_name, ignore_missing=True),
            'flavor', flavor_name)

        create_kwargs = {
            'name': f'{cluster_name_on_cloud}-head',
            'image_id': utils.get_attr(image, 'id'),
            'flavor_id': utils.get_attr(flavor, 'id'),
            'networks': [{
                'uuid': config.node_config['NetworkId'],
            }],
            'security_groups': [{
                'name': config.provider_config['_sky_security_group']['name'],
            }],
            'metadata': utils.instance_metadata(cluster_name_on_cloud,
                                                config.tags),
        }
        availability_zone = config.node_config.get('AvailabilityZone')
        if availability_zone:
            create_kwargs['availability_zone'] = availability_zone
        user_data = config.node_config.get('UserData')
        if user_data:
            create_kwargs['user_data'] = user_data

        server = connection.compute.create_server(**create_kwargs)
        server = _wait_for_server(connection, server, 'ACTIVE')
        if not config.provider_config.get('use_internal_ips', False):
            floating_ip = _ensure_floating_ip(connection, server,
                                              cluster_name_on_cloud,
                                              config.node_config)
        server_id = utils.get_attr(server, 'id')
        return _record(region,
                       cluster_name_on_cloud,
                       server_id,
                       created=[server_id],
                       zone=availability_zone)
    except Exception:
        _cleanup_failed_launch(connection, config, server, floating_ip)
        raise


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    """Returns Nova status for instances selected strictly by metadata."""
    del cluster_name, retry_if_missing
    assert provider_config is not None, cluster_name_on_cloud
    connection = utils.get_connection(provider_config,
                                      provider_config.get('region'))
    statuses = {}
    for server in utils.list_cluster_instances(connection,
                                               cluster_name_on_cloud):
        status, reason = utils.map_server_status(server)
        if non_terminated_only and status is None:
            continue
        statuses[utils.get_attr(server, 'id')] = (status, reason)
    return statuses


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    connection = utils.get_connection(region=region)
    servers = utils.list_cluster_instances(connection, cluster_name_on_cloud)
    if not servers:
        raise RuntimeError(
            f'OpenStack cluster {cluster_name_on_cloud!r} has no instances.')
    for server in servers:
        if utils.server_status(server) == 'ERROR':
            raise RuntimeError(utils.format_server_fault(server))
        if state == status_lib.ClusterStatus.UP:
            _wait_for_server(connection, server, 'ACTIVE')
        elif state == status_lib.ClusterStatus.STOPPED:
            _wait_for_server(connection, server, 'SHUTOFF')


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    assert provider_config is not None, cluster_name_on_cloud
    connection = utils.get_connection(provider_config,
                                      provider_config.get('region'))
    for server in utils.list_cluster_instances(connection,
                                               cluster_name_on_cloud):
        if worker_only and utils.is_head_instance(server):
            continue
        status = utils.server_status(server)
        if status == 'ERROR':
            raise RuntimeError(utils.format_server_fault(server))
        if status == 'ACTIVE':
            connection.compute.stop_server(server)


def _delete_managed_security_group(connection: Any, cluster_name: str,
                                   provider_config: Dict[str, Any]) -> None:
    if provider_config.get('security_group_name'):
        return
    security_group = utils.find_managed_security_group(connection, cluster_name)
    if security_group is None:
        return
    if (utils.get_attr(
            security_group,
            'description') == utils.security_group_description(cluster_name)):
        connection.network.delete_security_group(security_group,
                                                 ignore_missing=True)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Deletes only resources carrying SkyPilot ownership markers."""
    assert provider_config is not None, cluster_name_on_cloud
    connection = utils.get_connection(provider_config,
                                      provider_config.get('region'))
    servers = utils.list_cluster_instances(connection, cluster_name_on_cloud)
    selected_servers = [
        server for server in servers
        if not (worker_only and utils.is_head_instance(server))
    ]

    selected_ports = []
    for server in selected_servers:
        selected_ports.extend(
            utils.server_ports(connection, utils.get_attr(server, 'id')))
    floating_ips = utils.managed_floating_ips(
        connection, cluster_name_on_cloud,
        selected_ports if worker_only else None)
    for floating_ip in floating_ips:
        connection.network.delete_ip(floating_ip, ignore_missing=True)

    for server in selected_servers:
        connection.compute.delete_server(server, ignore_missing=True)
    for server in selected_servers:
        connection.compute.wait_for_delete(server,
                                           interval=_WAIT_INTERVAL_SECONDS,
                                           wait=_WAIT_TIMEOUT_SECONDS)

    if not worker_only:
        _delete_managed_security_group(connection, cluster_name_on_cloud,
                                       provider_config)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    provider_config = provider_config or {'region': region}
    connection = utils.get_connection(provider_config, region)
    servers = utils.list_cluster_instances(connection, cluster_name_on_cloud)
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for server in servers:
        if utils.server_status(server) in ('DELETED', 'SOFT_DELETED'):
            continue
        server_id = utils.get_attr(server, 'id')
        internal_ip, external_ip = utils.server_ips(server)
        if internal_ip is None:
            ports = utils.server_ports(connection, server_id)
            for port in ports:
                fixed_ips = utils.get_attr(port, 'fixed_ips', []) or []
                if fixed_ips:
                    internal_ip = utils.get_attr(fixed_ips[0], 'ip_address')
                    break
        if internal_ip is None:
            raise RuntimeError(
                f'OpenStack server {server_id!r} has no fixed IPv4 address.')
        if not provider_config.get('use_internal_ips',
                                   False) and external_ip is None:
            ports = utils.server_ports(connection, server_id)
            floating_ips = utils.managed_floating_ips(connection,
                                                      cluster_name_on_cloud,
                                                      ports)
            if floating_ips:
                external_ip = utils.get_attr(floating_ips[0],
                                             'floating_ip_address')
        instances[server_id] = [
            common.InstanceInfo(instance_id=server_id,
                                internal_ip=internal_ip,
                                external_ip=external_ip,
                                tags=utils.metadata(server),
                                node_name=utils.get_attr(server, 'name'))
        ]
        if utils.is_head_instance(server):
            head_instance_id = server_id
    return common.ClusterInfo(instances=instances,
                              head_instance_id=head_instance_id,
                              provider_name='openstack',
                              provider_config=provider_config)

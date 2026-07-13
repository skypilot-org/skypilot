"""Utilities shared by the OpenStack provisioner."""

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sky import skypilot_config
from sky.adaptors import openstack as openstack_adaptor
from sky.provision import constants
from sky.utils import status_lib

TAG_SKYPILOT_MANAGED_RESOURCE = 'skypilot-managed-resource'
MANAGED_INSTANCE_VALUE = 'instance'
MANAGED_SECURITY_GROUP_DESCRIPTION = (
    'SkyPilot managed security group for cluster {cluster_name}')
MANAGED_FLOATING_IP_DESCRIPTION = (
    'SkyPilot managed floating IP for cluster {cluster_name}')
MANAGED_RESOURCE_TAG = 'skypilot-managed'


def get_connection(
    provider_config: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
) -> Any:
    """Returns a connection for the configured named cloud and region."""
    cloud = None
    if provider_config is not None:
        cloud = provider_config.get('cloud')
        region = region or provider_config.get('region')
    if not cloud:
        cloud = skypilot_config.get_nested(keys=('openstack', 'cloud'),
                                           default_value=None)
    if not cloud:
        raise ValueError('OpenStack requires a named clouds.yaml profile in '
                         'openstack.cloud.')
    return openstack_adaptor.get_connection(cloud, region)


def get_attr(resource: Any, key: str, default: Any = None) -> Any:
    """Reads a field from an SDK resource or plain dictionary."""
    if isinstance(resource, dict):
        return resource.get(key, default)
    value = getattr(resource, key, default)
    if value is not default:
        return value
    getter = getattr(resource, 'get', None)
    if getter is not None:
        return getter(key, default)
    return default


def managed_security_group_name(cluster_name: str) -> str:
    return _bounded_resource_name(f'skypilot-{cluster_name}')


def _bounded_resource_name(name: str, limit: int = 255) -> str:
    if len(name) <= limit:
        return name
    digest = hashlib.sha256(name.encode()).hexdigest()[:12]
    return f'{name[:limit - len(digest) - 1]}-{digest}'


def security_group_description(cluster_name: str) -> str:
    return MANAGED_SECURITY_GROUP_DESCRIPTION.format(cluster_name=cluster_name)


def floating_ip_description(cluster_name: str) -> str:
    return MANAGED_FLOATING_IP_DESCRIPTION.format(cluster_name=cluster_name)


def managed_resource_tags(cluster_name: str, resource: str) -> List[str]:
    cluster_digest = hashlib.sha256(cluster_name.encode()).hexdigest()[:16]
    return [
        MANAGED_RESOURCE_TAG, f'skypilot-cluster-{cluster_digest}',
        f'skypilot-resource-{resource}'
    ]


def has_managed_resource_tags(resource_obj: Any, cluster_name: str,
                              resource: str) -> bool:
    tags = get_attr(resource_obj, 'tags', []) or []
    return set(managed_resource_tags(cluster_name,
                                     resource)).issubset(set(tags))


def current_project_id(connection: Any) -> str:
    project_id = getattr(connection, 'current_project_id', None)
    if not project_id:
        session = getattr(connection, 'session', None)
        get_project_id = getattr(session, 'get_project_id', None)
        if callable(get_project_id):
            project_id = get_project_id()
    if not project_id:
        raise ValueError('OpenStack project ID could not be determined.')
    return str(project_id)


def is_current_project_resource(connection: Any, resource_obj: Any) -> bool:
    resource_project_id = (get_attr(resource_obj, 'project_id') or
                           get_attr(resource_obj, 'tenant_id'))
    return (resource_project_id is not None and
            str(resource_project_id) == current_project_id(connection))


def find_managed_security_group(connection: Any, cluster_name: str) -> Any:
    name = managed_security_group_name(cluster_name)
    matches = [
        security_group
        for security_group in connection.network.security_groups(name=name)
        if get_attr(security_group, 'name') == name and
        is_current_project_resource(connection, security_group)
    ]
    if len(matches) > 1:
        raise RuntimeError(f'Found multiple OpenStack security groups named '
                           f'{name!r} in the current project.')
    return matches[0] if matches else None


def instance_metadata(cluster_name: str,
                      tags: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    result = {str(key): str(value) for key, value in (tags or {}).items()}
    result.update({
        constants.TAG_RAY_CLUSTER_NAME: cluster_name,
        constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name,
        **constants.HEAD_NODE_TAGS,
        TAG_SKYPILOT_MANAGED_RESOURCE: MANAGED_INSTANCE_VALUE,
    })
    return result


def metadata(server: Any) -> Dict[str, str]:
    value = get_attr(server, 'metadata', {})
    return value if isinstance(value, dict) else {}


def is_cluster_instance(server: Any, cluster_name: str) -> bool:
    server_metadata = metadata(server)
    return (server_metadata.get(
        constants.TAG_SKYPILOT_CLUSTER_NAME) == cluster_name and
            server_metadata.get(TAG_SKYPILOT_MANAGED_RESOURCE)
            == MANAGED_INSTANCE_VALUE)


def is_head_instance(server: Any) -> bool:
    server_metadata = metadata(server)
    return (server_metadata.get(constants.TAG_SKYPILOT_HEAD_NODE) == '1' or
            server_metadata.get(constants.TAG_RAY_NODE_KIND) == 'head')


def list_cluster_instances(connection: Any, cluster_name: str) -> List[Any]:
    servers = connection.compute.servers(details=True)
    return [
        server for server in servers
        if is_cluster_instance(server, cluster_name) and
        is_current_project_resource(connection, server)
    ]


def server_status(server: Any) -> str:
    return str(get_attr(server, 'status', '')).upper()


def format_server_fault(server: Any) -> str:
    fault = get_attr(server, 'fault')
    if isinstance(fault, dict):
        message = fault.get('message') or fault.get('details') or str(fault)
    elif fault:
        message = str(fault)
    else:
        message = 'unknown Nova fault'
    return (f'OpenStack server {get_attr(server, "id", "<unknown>")} '
            f'entered ERROR: {message}')


def map_server_status(
        server: Any
) -> Tuple[Optional[status_lib.ClusterStatus], Optional[str]]:
    status = server_status(server)
    if status == 'ACTIVE':
        return status_lib.ClusterStatus.UP, None
    if status in ('SHUTOFF', 'STOPPED'):
        return status_lib.ClusterStatus.STOPPED, None
    if status in ('DELETED', 'SOFT_DELETED'):
        return None, None
    if status == 'ERROR':
        return status_lib.ClusterStatus.INIT, format_server_fault(server)
    return status_lib.ClusterStatus.INIT, None


def server_ips(server: Any) -> Tuple[Optional[str], Optional[str]]:
    """Returns the first fixed IPv4 and floating IPv4 on a server."""
    addresses = get_attr(server, 'addresses', {}) or {}
    internal_ip = None
    external_ip = None
    for network_addresses in addresses.values():
        for address in network_addresses or []:
            version = get_attr(address, 'version')
            if version not in (None, 4, '4'):
                continue
            ip_address = get_attr(address, 'addr')
            address_type = get_attr(address, 'OS-EXT-IPS:type')
            if address_type == 'floating':
                external_ip = external_ip or ip_address
            elif address_type == 'fixed' or address_type is None:
                internal_ip = internal_ip or ip_address
    return internal_ip, external_ip


def server_ports(connection: Any, server_id: str) -> List[Any]:
    return list(connection.network.ports(device_id=server_id))


def managed_floating_ips(connection: Any,
                         cluster_name: str,
                         ports: Optional[Iterable[Any]] = None) -> List[Any]:
    expected_description = floating_ip_description(cluster_name)
    port_ids = None
    if ports is not None:
        port_ids = {get_attr(port, 'id') for port in ports}
    result = []
    for floating_ip in connection.network.ips():
        if not is_current_project_resource(connection, floating_ip):
            continue
        if get_attr(floating_ip, 'description') != expected_description:
            continue
        if port_ids is not None and get_attr(floating_ip,
                                             'port_id') not in port_ids:
            continue
        result.append(floating_ip)
    return result

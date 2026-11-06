"""UpCloud API utilities."""
import enum
import ipaddress
import random
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from sky import sky_logging
from sky.adaptors import upcloud as upcloud_adaptor
from sky.utils import annotations
from sky.utils import auth_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib

POLL_INTERVAL = 5
TIMEOUT = 900
LABEL_CLUSTER = 'skypilot-cluster'
LABEL_ROLE = 'skypilot-role'
LABEL_VOLUME = 'skypilot-volume'
DEFAULT_SSH_USER = 'ubuntu'

GPU_PLAN_PREFIX = 'GPU-'
SPOT_PLAN_PREFIX = 'GPU-SPOT-'

# Prefix length of the subnet given to each private network (cluster or file
# storage).
CLUSTER_NETWORK_PREFIXLEN = 22
_MAX_NETWORK_CREATE_ATTEMPTS = 5
_MAX_API_ATTEMPTS = 3
# UpCloud's resolvers, handed out by DHCP. The public firewall is stateless,
# so their replies need explicit accept rules or name resolution fails.
UPCLOUD_DNS_SERVERS = {
    'IPv4': ('94.237.127.9', '94.237.40.9'),
    'IPv6': ('2a04:3540:53::1', '2a04:3544:53::1'),
}

logger = sky_logging.init_logger(__name__)

_DISK_TIER_TO_UPCLOUD = {
    resources_utils.DiskTier.LOW: 'hdd',
    resources_utils.DiskTier.MEDIUM: 'standard',
    resources_utils.DiskTier.HIGH: 'maxiops',
    resources_utils.DiskTier.BEST: 'maxiops',
}

UpCloudError = upcloud_adaptor.UpCloudError


def allocate_cidr(seed: str, used_cidrs: Iterable[str]) -> str:
    """Picks a free private network subnet for ``seed``.

    The choice is deterministic for a given seed, so a cluster gets the same
    subnet whenever it is re-created. UpCloud rejects a private network whose
    subnet overlaps an existing one in the zone, so ``used_cidrs`` are skipped.
    """
    used = [ipaddress.ip_network(c, strict=False) for c in used_cidrs]
    candidates = [
        f'10.{second}.{third}.0/{CLUSTER_NETWORK_PREFIXLEN}'
        for second in range(1, 255)
        # A /22 starts at a multiple of 4 in the third octet.
        for third in range(0, 256, 4)
    ]
    random.Random(seed).shuffle(candidates)
    for cidr in candidates:
        network = ipaddress.ip_network(cidr)
        if not any(network.overlaps(u) for u in used):
            return cidr
    raise UpCloudError(f'No free private network subnet left for {seed!r}.')


def network_cidrs(network: Dict[str, Any]) -> List[str]:
    """Returns the IPv4 subnets of a network payload."""
    ip_block = network.get('ip_networks') or {}
    return [
        str(item['address'])
        for item in _as_list(ip_block.get('ip_network', []))
        if item.get('address')
    ]


def create_network_in_free_range(
        client: 'UpCloudClient', name: str, zone: str, seed: str,
        labels: Dict[str, str]) -> Tuple[Dict[str, Any], str]:
    """Creates a private network in ``zone`` on a subnet no network uses.

    Another launch may claim the subnet between listing and creating, which
    UpCloud reports as ``ADDRESS_INVALID``; the next free subnet is tried then.
    Returns the created network and its CIDR.
    """
    rejected: List[str] = []
    last_error: Optional[UpCloudError] = None
    for _ in range(_MAX_NETWORK_CREATE_ATTEMPTS):
        used = rejected + [
            cidr for zone_network in client.list_networks(zone=zone)
            for cidr in network_cidrs(zone_network)
        ]
        cidr = allocate_cidr(seed, used)
        try:
            network = client.create_network(name, zone, cidr, labels)
        except UpCloudError as e:
            if 'ADDRESS_INVALID' not in str(e):
                raise
            logger.debug(f'Private network range {cidr} is in use; trying '
                         'another one.')
            rejected.append(cidr)
            last_error = e
            continue
        return network, cidr
    raise UpCloudError(f'Could not find a free private network range in {zone} '
                       f'for {seed!r}: {last_error}')


class UpCloudInstanceStatus(enum.Enum):
    """Statuses for UpCloud servers."""
    STARTED = 'started'
    STOPPED = 'stopped'
    MAINTENANCE = 'maintenance'
    ERROR = 'error'
    PENDING_DELETE = 'pending_delete'

    @classmethod
    def from_raw_status(cls, status: str) -> 'UpCloudInstanceStatus':
        try:
            return cls(status.lower())
        except ValueError as exc:
            raise UpCloudError(f'Unknown instance status: {status}') from exc

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        mapping = {
            self.STARTED: status_lib.ClusterStatus.UP,
            self.STOPPED: status_lib.ClusterStatus.STOPPED,
            self.MAINTENANCE: status_lib.ClusterStatus.INIT,
            self.ERROR: status_lib.ClusterStatus.INIT,
            self.PENDING_DELETE: None,
        }
        return mapping.get(self)


def is_spot_plan(plan: str) -> bool:
    return plan.upper().startswith(SPOT_PLAN_PREFIX)


def spot_plan_name(plan: str) -> str:
    """Returns the spot plan name for an on-demand GPU plan."""
    if is_spot_plan(plan):
        return plan
    if not plan.upper().startswith(GPU_PLAN_PREFIX):
        raise UpCloudError(f'Plan {plan} has no spot variant on UpCloud.')
    return SPOT_PLAN_PREFIX + plan[len(GPU_PLAN_PREFIX):]


def on_demand_plan_name(plan: str) -> str:
    """Returns the on-demand plan name for a spot GPU plan."""
    if not is_spot_plan(plan):
        return plan
    return GPU_PLAN_PREFIX + plan[len(SPOT_PLAN_PREFIX):]


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    return [item for item in (value or []) if isinstance(item, dict)]


def get_labels(resource: Dict[str, Any]) -> Dict[str, str]:
    """Returns the labels of a resource as a dict.

    Servers, server groups and networks wrap labels as {'label': [...]};
    storages and file storages return a plain list.
    """
    labels = resource.get('labels') or []
    if isinstance(labels, dict):
        labels = labels.get('label', [])
    return {
        str(label.get('key')): str(label.get('value', ''))
        for label in _as_list(labels)
    }


def has_label(resource: Dict[str, Any], key: str, value: str) -> bool:
    return get_labels(resource).get(key) == value


def labels_as_list(
        cluster_name: str,
        role: str,
        user_labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build an UpCloud labels payload; SkyPilot labels override user ones."""
    labels = dict(user_labels or {})
    labels[LABEL_CLUSTER] = cluster_name
    labels[LABEL_ROLE] = role
    return {
        'label': [{
            'key': key,
            'value': str(value)
        } for key, value in labels.items()]
    }


def cluster_labels(cluster_name: str) -> Dict[str, Any]:
    return {'label': [{'key': LABEL_CLUSTER, 'value': cluster_name}]}


class UpCloudClient:
    """Minimal UpCloud API 1.3 client."""

    def __init__(self, token: Optional[str] = None):
        self._session = (upcloud_adaptor.client() if token is None else
                         upcloud_adaptor.make_client(token))

    def _request(self,
                 method: str,
                 path: str,
                 payload: Optional[Dict[str, Any]] = None,
                 params: Optional[Dict[str, Any]] = None) -> Any:
        backoff = common_utils.Backoff(initial_backoff=1)
        idempotent = method in ('GET', 'DELETE')
        for attempt in range(1, _MAX_API_ATTEMPTS + 1):
            last_attempt = attempt == _MAX_API_ATTEMPTS
            try:
                response = self._session.request(
                    method,
                    f'{upcloud_adaptor.API_BASE}{path}',
                    json=payload,
                    params=params,
                    timeout=120)
            except requests.exceptions.RequestException as e:
                if not idempotent or last_attempt:
                    raise UpCloudError(f'UpCloud request failed: {e}') from e
                time.sleep(backoff.current_backoff())
                continue
            # The API did not run a 429 request. A 5xx POST may have run, so
            # retrying it could create a second server.
            if last_attempt or not (response.status_code == 429 or
                                    (idempotent and
                                     response.status_code >= 500)):
                break
            logger.debug(f'UpCloud API {method} {path} returned '
                         f'{response.status_code}; retrying.')
            time.sleep(backoff.current_backoff())
        if response.status_code == 204:
            return {}
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {'raw': response.text}
        if not response.ok:
            detail = data.get('error', data) if isinstance(data, dict) else data
            raise UpCloudError(f'UpCloud API {method} {path} failed '
                               f'({response.status_code}): {detail}')
        return data

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET an UpCloud API path and return the decoded JSON body."""
        return self._request('GET', path, params=params)

    @staticmethod
    def _items(data: Dict[str, Any], container: str,
               item: str) -> List[Dict[str, Any]]:
        values = (data.get(container) or {}).get(item, [])
        return [values] if isinstance(values, dict) else list(values or [])

    def _list(self,
              path: str,
              container: str,
              item: str,
              params: Optional[Dict[str, Any]] = None,
              paginated: bool = True) -> List[Dict[str, Any]]:
        params = dict(params or {})
        if not paginated:
            return self._items(self._request('GET', path, params=params),
                               container, item)
        page_size = 100
        offset = 0
        result: List[Dict[str, Any]] = []
        while True:
            page = self._items(
                self._request('GET',
                              path,
                              params={
                                  **params, 'limit': page_size,
                                  'offset': offset
                              }), container, item)
            result.extend(page)
            if len(page) < page_size:
                return result
            offset += page_size

    def get_account(self) -> Dict[str, Any]:
        return self._request('GET', '/account')

    # Servers

    def list_servers(
        self,
        cluster_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = {'label': f'{LABEL_CLUSTER}={cluster_name}'} if cluster_name \
            else None
        return self._list('/server', 'servers', 'server', params)

    def get_server(self, uuid: str) -> Dict[str, Any]:
        return self._request('GET', f'/server/{uuid}').get('server', {})

    def create_server(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request('POST', '/server', {
            'server': body
        }).get('server', {})

    def start_server(self, uuid: str) -> Dict[str, Any]:
        return self._request('POST', f'/server/{uuid}/start').get('server', {})

    def stop_server(self, uuid: str, stop_type: str = 'soft') -> Dict[str, Any]:
        return self._request('POST', f'/server/{uuid}/stop', {
            'stop_server': {
                'stop_type': stop_type,
                'timeout': '60',
            }
        }).get('server', {})

    def delete_server(self, uuid: str) -> None:
        self._request('DELETE',
                      f'/server/{uuid}',
                      params={
                          'storages': 'yes',
                          'backups': 'delete'
                      })

    def modify_server(self, uuid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request('PUT', f'/server/{uuid}', {
            'server': body
        }).get('server', {})

    # Firewall

    def list_firewall_rules(self, uuid: str) -> List[Dict[str, Any]]:
        data = self._request('GET', f'/server/{uuid}/firewall_rule')
        return self._items(data, 'firewall_rules', 'firewall_rule')

    def replace_firewall_rules(self, uuid: str, rules: List[Dict[str,
                                                                 Any]]) -> None:
        """Atomically replaces the server's whole firewall rule chain."""
        self._request('PUT', f'/server/{uuid}/firewall_rule',
                      {'firewall_rules': {
                          'firewall_rule': rules
                      }})

    # Server groups

    def create_server_group(self, title: str,
                            cluster_name: str) -> Dict[str, Any]:
        return self._request(
            'POST', '/server-group', {
                'server_group': {
                    'title': title[:255],
                    'anti_affinity': 'yes',
                    'labels': cluster_labels(cluster_name),
                }
            }).get('server_group', {})

    def list_server_groups(self,
                           cluster_name: Optional[str] = None
                          ) -> List[Dict[str, Any]]:
        groups = self._list('/server-group',
                            'server_groups',
                            'server_group',
                            paginated=False)
        if cluster_name is None:
            return groups
        return [g for g in groups if has_label(g, LABEL_CLUSTER, cluster_name)]

    def delete_server_group(self, uuid: str) -> None:
        self._request('DELETE', f'/server-group/{uuid}')

    # Networks

    def create_network(self, name: str, zone: str, cidr: str,
                       labels: Dict[str, str]) -> Dict[str, Any]:
        return self._request(
            'POST', '/network', {
                'network': {
                    'type': 'private',
                    'name': name[:255],
                    'zone': zone,
                    'ip_networks': {
                        'ip_network': [{
                            'family': 'IPv4',
                            'address': cidr,
                            'dhcp': 'yes',
                            'dhcp_default_route': 'no',
                        }]
                    },
                    'labels': [{
                        'key': k,
                        'value': v
                    } for k, v in labels.items()]
                }
            }).get('network', {})

    def get_network(self, uuid: str) -> Dict[str, Any]:
        return self._request('GET', f'/network/{uuid}').get('network', {})

    def list_networks(self,
                      cluster_name: Optional[str] = None,
                      zone: Optional[str] = None) -> List[Dict[str, Any]]:
        networks = self._list('/network',
                              'networks',
                              'network',
                              paginated=False)
        if cluster_name is not None:
            networks = [
                n for n in networks if has_label(n, LABEL_CLUSTER, cluster_name)
            ]
        if zone is not None:
            networks = [n for n in networks if n.get('zone') == zone]
        return networks

    def delete_network(self, uuid: str) -> None:
        self._request('DELETE', f'/network/{uuid}')

    # Storage

    def get_storage(self, uuid: str) -> Dict[str, Any]:
        return self._request('GET', f'/storage/{uuid}').get('storage', {})

    def detach_storage(self, server_uuid: str, address: str) -> None:
        self._request('POST', f'/server/{server_uuid}/storage/detach',
                      {'storage_device': {
                          'address': address
                      }})

    def templatize_storage(self, uuid: str, title: str) -> Dict[str, Any]:
        return self._request('POST', f'/storage/{uuid}/templatize', {
            'storage': {
                'title': title
            }
        }).get('storage', {})

    def clone_storage(self,
                      uuid: str,
                      zone: str,
                      title: str,
                      tier: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            'zone': zone,
            'title': title,
        }
        if tier:
            body['tier'] = tier
        return self._request('POST', f'/storage/{uuid}/clone', {
            'storage': body
        }).get('storage', {})

    def delete_storage(self, uuid: str) -> None:
        self._request('DELETE', f'/storage/{uuid}')

    def list_public_templates(self) -> List[Dict[str, Any]]:
        return self._list('/storage/template', 'storages', 'storage')

    # File storage

    def create_file_storage(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request('POST', '/file-storage', {
            'file_storage': body
        }).get('file_storage', {})

    def get_file_storage(self, uuid: str) -> Dict[str, Any]:
        return self._request('GET',
                             f'/file-storage/{uuid}').get('file_storage', {})

    def list_file_storages(self) -> List[Dict[str, Any]]:
        return self._list('/file-storage', 'file_storages', 'file_storage')

    def delete_file_storage(self, uuid: str) -> None:
        self._request('DELETE', f'/file-storage/{uuid}')


def _iter_ip_addresses(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    ip_block = server.get('ip_addresses') or {}
    return _as_list(ip_block.get('ip_address', []))


def _iter_interfaces(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    networking = server.get('networking') or {}
    interfaces = networking.get('interfaces') or {}
    return _as_list(interfaces.get('interface', []))


def get_public_ip(server: Dict[str, Any]) -> Optional[str]:
    """Return the public IPv4 address of a server payload."""
    for item in _iter_ip_addresses(server):
        family = str(item.get('family', 'IPv4')).lower()
        access = str(item.get('access', '')).lower()
        if family == 'ipv4' and access == 'public' and item.get('address'):
            return str(item['address'])
    return None


def get_private_ip(server: Dict[str, Any],
                   network_uuid: Optional[str] = None) -> Optional[str]:
    """Return the IPv4 address of the server on an SDN private network."""
    for interface in _iter_interfaces(server):
        if str(interface.get('type', '')).lower() != 'private':
            continue
        if network_uuid is not None and interface.get('network') != (
                network_uuid):
            continue
        ip_block = interface.get('ip_addresses') or {}
        for item in _as_list(ip_block.get('ip_address', [])):
            if (str(item.get('family', 'IPv4')).lower() == 'ipv4' and
                    item.get('address')):
                return str(item['address'])
    for item in _iter_ip_addresses(server):
        family = str(item.get('family', 'IPv4')).lower()
        access = str(item.get('access', '')).lower()
        if family == 'ipv4' and access == 'private' and item.get('address'):
            return str(item['address'])
    return None


def disk_tier_to_upcloud(disk_tier: Optional[resources_utils.DiskTier]) -> str:
    """Map a SkyPilot disk tier to an UpCloud storage_tier value."""
    if disk_tier is None:
        disk_tier = resources_utils.DiskTier.MEDIUM
    return _DISK_TIER_TO_UPCLOUD.get(disk_tier, 'standard')


def get_storage_devices(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _as_list((server.get('storage_devices') or
                     {}).get('storage_device', []))


def get_boot_storage_uuid(server: Dict[str, Any]) -> str:
    """Return the OS disk UUID from a server payload."""
    fallback: Optional[str] = None
    for device in get_storage_devices(server):
        uuid = device.get('storage') or device.get('uuid')
        if not uuid:
            continue
        uuid_s = str(uuid)
        device_type = str(device.get('type', 'disk')).lower()
        if device_type not in ('disk', 'storage', ''):
            continue
        if str(device.get('part_of_plan', '')).lower() == 'yes':
            return uuid_s
        if str(device.get('boot_disk', '')) in ('1', 'yes', 'True'):
            return uuid_s
        if fallback is None:
            fallback = uuid_s
    if fallback:
        return fallback
    raise UpCloudError(f'No boot disk UUID on server {server.get("uuid")}')


def _wait(fetch, uuid: str, kind: str, desired: List[str],
          timeout: int) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    desired_l = [s.lower() for s in desired]
    while time.time() < deadline:
        last = fetch(uuid)
        state = str(last.get('state', '')).lower()
        if state in desired_l:
            return last
        if state == 'error':
            raise UpCloudError(f'{kind} {uuid} entered error state: {last}')
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f'Timed out waiting for {kind.lower()} {uuid} to reach '
                       f'{desired} (last={last.get("state")})')


def wait_for_storage_state(client: UpCloudClient,
                           uuid: str,
                           desired: List[str],
                           timeout: int = TIMEOUT) -> Dict[str, Any]:
    return _wait(client.get_storage, uuid, 'Storage', desired, timeout)


def wait_for_state(client: UpCloudClient,
                   uuid: str,
                   desired: List[str],
                   timeout: int = TIMEOUT) -> Dict[str, Any]:
    return _wait(client.get_server, uuid, 'Server', desired, timeout)


def wait_for_states(client: UpCloudClient,
                    uuids: Iterable[str],
                    desired: List[str],
                    timeout: int = TIMEOUT) -> Dict[str, Dict[str, Any]]:
    """Waits until all servers reach one of the desired states."""
    deadline = time.time() + timeout
    desired_l = [s.lower() for s in desired]
    pending = set(uuids)
    done: Dict[str, Dict[str, Any]] = {}
    while pending:
        for uuid in sorted(pending):
            server = client.get_server(uuid)
            state = str(server.get('state', '')).lower()
            if state in desired_l:
                done[uuid] = server
            elif state == 'error':
                raise UpCloudError(
                    f'Server {uuid} entered error state: {server}')
        pending -= set(done)
        if not pending:
            break
        if time.time() > deadline:
            raise TimeoutError(
                f'Timed out waiting for servers {sorted(pending)} to reach '
                f'{desired}')
        time.sleep(POLL_INTERVAL)
    return done


def sanitize_hostname(cluster_name: str, role: str) -> str:
    base = re.sub(r'[^a-z0-9-]', '-', cluster_name.lower())
    base = re.sub(r'-+', '-', base).strip('-') or 'sky'
    suffix = f'-{role}'
    return (base[:63 - len(suffix)].strip('-') or 'sky') + suffix


def _template_version(title: str) -> Optional[Tuple[int, int]]:
    match = re.search(r'\b(\d{2})\.(\d{2})\b', title)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _is_gpu_template(title: str) -> bool:
    lowered = title.lower()
    return 'nvidia' in lowered or 'cuda' in lowered


def pick_ubuntu_template(templates: List[Dict[str, Any]],
                         gpu: bool) -> Tuple[str, str]:
    """Picks the newest Ubuntu LTS public template of the requested kind.

    GPU plans use the template with NVIDIA drivers, CUDA, Docker and the
    NVIDIA Container Toolkit; CPU plans use plain Ubuntu Server.

    Returns:
        A (uuid, title) tuple.
    """
    candidates = []
    for storage in templates:
        title = str(storage.get('title', ''))
        uuid = storage.get('uuid')
        version = _template_version(title)
        if (not uuid or 'ubuntu' not in title.lower() or version is None or
                version[1] != 4 or _is_gpu_template(title) != gpu):
            continue
        candidates.append((version, title, str(uuid)))
    if not candidates:
        kind = 'GPU (NVIDIA drivers & CUDA)' if gpu else 'Ubuntu Server'
        titles = sorted(str(t.get('title', '')) for t in templates)
        raise UpCloudError(f'Could not find a public {kind} Ubuntu LTS '
                           f'template. Titles seen: {titles[:20]}')
    # Newest LTS first; title breaks ties deterministically.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, title, uuid = candidates[0]
    return uuid, title


@annotations.lru_cache(scope='request')
def resolve_template_uuid(client: UpCloudClient, gpu: bool) -> str:
    """Returns the default Ubuntu template UUID for GPU or CPU plans."""
    uuid, title = pick_ubuntu_template(client.list_public_templates(), gpu)
    logger.debug(f'Using UpCloud template {title} ({uuid}).')
    return uuid


def _rule(direction: str,
          action: str,
          comment: str,
          family: Optional[str] = None,
          protocol: Optional[str] = None,
          port_start: Optional[int] = None,
          port_end: Optional[int] = None,
          source_port_start: Optional[int] = None,
          source_port_end: Optional[int] = None,
          source_address: Optional[str] = None) -> Dict[str, Any]:
    rule: Dict[str, Any] = {
        'direction': direction,
        'action': action,
        'comment': comment,
    }
    if family is not None:
        rule['family'] = family
    if protocol is not None:
        rule['protocol'] = protocol
    if port_start is not None:
        rule['destination_port_start'] = str(port_start)
        rule['destination_port_end'] = str(
            port_end if port_end is not None else port_start)
    if source_port_start is not None:
        rule['source_port_start'] = str(source_port_start)
        rule['source_port_end'] = str(source_port_end if source_port_end
                                      is not None else source_port_start)
    if source_address is not None:
        rule['source_address_start'] = source_address
        rule['source_address_end'] = source_address
    return rule


def parse_port_ranges(ports: Iterable[Any]) -> List[Tuple[int, int]]:
    """Parses SkyPilot port specs (8080, '8080', '8000-8010') into ranges."""
    ranges: List[Tuple[int, int]] = []
    for spec in ports:
        text = str(spec).strip()
        if '-' in text:
            start_s, end_s = text.split('-', 1)
            item = (int(start_s), int(end_s))
        else:
            item = (int(text), int(text))
        if item not in ranges:
            ranges.append(item)
    return ranges


def build_firewall_rules(
        port_ranges: Iterable[Tuple[int, int]]) -> List[Dict[str, Any]]:
    """Returns the full firewall chain for a SkyPilot node.

    The firewall applies to public interfaces only (intra-cluster traffic uses
    the SDN private network). Inbound: SSH, the requested TCP ports, replies
    from UpCloud's DNS resolvers, ICMP and DHCP; everything else is dropped.
    Ephemeral ports are not implicitly opened: services on those ports require
    an explicit port request too.
    """
    ranges = [(22, 22)]
    for item in port_ranges:
        if item not in ranges:
            ranges.append(item)
    rules: List[Dict[str, Any]] = []
    for family in ('IPv4', 'IPv6'):
        for start, end in ranges:
            name = 'ssh' if (start, end) == (22, 22) else f'port-{start}-{end}'
            rules.append(
                _rule('in', 'accept', f'skypilot-{name}', family, 'tcp', start,
                      end))
        for server in UPCLOUD_DNS_SERVERS[family]:
            for protocol in ('udp', 'tcp'):
                rules.append(
                    _rule('in',
                          'accept',
                          'skypilot-dns',
                          family,
                          protocol,
                          source_port_start=53,
                          source_address=server))
        # The API protocol enum is tcp, udp, icmp. IPv6 ICMP is icmp plus
        # family IPv6; icmp6 is rejected.
        rules.append(_rule('in', 'accept', 'skypilot-icmp', family, 'icmp'))
    rules.append(
        _rule('in',
              'accept',
              'skypilot-dhcp',
              'IPv4',
              'udp',
              68,
              source_port_start=67))
    rules.append(_rule('in', 'drop', 'skypilot-default-in'))
    rules.append(_rule('out', 'accept', 'skypilot-default-out'))
    return rules


def opened_port_ranges(rules: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Returns the user-requested port ranges present in a SkyPilot chain."""
    ranges: List[Tuple[int, int]] = []
    for rule in rules:
        comment = str(rule.get('comment') or '')
        if not comment.startswith('skypilot-port-'):
            continue
        try:
            item = (int(rule['destination_port_start']),
                    int(rule['destination_port_end']))
        except (KeyError, TypeError, ValueError):
            continue
        if item not in ranges:
            ranges.append(item)
    return ranges


def read_public_key() -> str:
    _, public_key_path = auth_utils.get_or_generate_keys()
    with open(public_key_path, 'r', encoding='utf-8') as f:
        return f.read().strip()

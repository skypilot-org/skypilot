"""Lium API helpers, shared by the provisioner and the catalog fetcher."""

import configparser
import dataclasses
import os
import re
import time
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

logger = sky_logging.init_logger(__name__)

DEFAULT_BASE_URL = 'https://lium.io/api'
# The tier of a node that keeps running until the renter stops it. The other
# tier, 'spot', is taken back when its provider wants the node.
SECURE_TIER = 'secure'
CREDENTIAL_PATH = '~/.lium/config.ini'
API_KEY_ENV_VAR = 'LIUM_API_KEY'

_REQUEST_TIMEOUT = 30
_POD_POLL_INTERVAL = 10

# Maps the GPU model names that Lium reports to the accelerator names that
# SkyPilot uses. A model that is absent here never reaches the catalog, so a
# new GPU on Lium cannot land under a name SkyPilot does not know.
#
# The names are the ones without a vendor prefix. The node API and the public
# node feed print the same model differently ('NVIDIA GeForce RTX 5090' and
# 'RTX 5090'), so accelerator_name() removes the prefixes before the lookup.
GPU_NAME_MAP: Dict[str, str] = {
    'A100 80GB PCIe': 'A100-80GB',
    'A100-SXM4-80GB': 'A100-80GB-SXM',
    'A40': 'A40',
    'B200': 'B200',
    'B300 SXM6 AC': 'B300',
    'H100 80GB HBM3': 'H100-SXM',
    'H100 PCIe': 'H100',
    'H200': 'H200',
    'H200 NVL': 'H200',
    'L4': 'L4',
    'L40': 'L40',
    'L40S': 'L40S',
    'RTX 3080': 'RTX3080',
    'RTX 3090': 'RTX3090',
    'RTX 4080': 'RTX4080',
    'RTX 4090': 'RTX4090',
    'RTX 5070 Ti': 'RTX5070Ti',
    'RTX 5080': 'RTX5080',
    'RTX 5090': 'RTX5090',
    'RTX 6000 Ada Generation': 'RTX6000-Ada',
    'RTX A4000': 'RTXA4000',
    'RTX A5000': 'RTXA5000',
    'RTX A6000': 'RTXA6000',
    'RTX PRO 6000 Blackwell Server Edition': 'RTXPRO6000',
    'RTX PRO 6000 Blackwell Workstation Edition': 'RTXPRO6000-WK',
}

# Lium pod states, mapped to the SkyPilot cluster states. A pod that failed
# maps to None: SkyPilot then reports the cluster as failed and cleans it up.
POD_STATUS_MAP: Dict[str, Optional[status_lib.ClusterStatus]] = {
    'PENDING': status_lib.ClusterStatus.INIT,
    'START_PENDING': status_lib.ClusterStatus.INIT,
    'REBOOT_PENDING': status_lib.ClusterStatus.INIT,
    'RUNNING': status_lib.ClusterStatus.UP,
    'STOP_PENDING': status_lib.ClusterStatus.STOPPED,
    'STOPPED': status_lib.ClusterStatus.STOPPED,
    'DELETING': status_lib.ClusterStatus.STOPPED,
    'FAILED': None,
    'CREATION_FAILED': None,
    'REBOOT_FAILED': None,
    'BROKEN': None,
}


class LiumError(Exception):
    """Raised when the Lium API rejects a request."""


@dataclasses.dataclass
class LiumNode:
    """A node that Lium offers for rent."""
    id: str
    gpu_model: str
    gpu_count: int
    driver_version: str
    price_per_hour: float
    country_code: str
    tier: str
    is_whole_host_free: bool


@dataclasses.dataclass
class LiumPod:
    """A pod that runs on a rented node."""
    id: str
    name: str
    status: str
    host: Optional[str]
    ssh_port: int


def read_api_key() -> Optional[str]:
    """Returns the API key from the environment or the credential file."""
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if api_key:
        return api_key
    credential_path = os.path.expanduser(CREDENTIAL_PATH)
    if not os.path.exists(credential_path):
        return None
    config = configparser.ConfigParser()
    config.read(credential_path)
    return config.get('api', 'api_key', fallback=None)


def _request(method: str,
             path: str,
             params: Optional[Dict[str, Any]] = None,
             json: Optional[Dict[str, Any]] = None) -> Any:
    """Calls the Lium API and returns the decoded body."""
    api_key = read_api_key()
    if api_key is None:
        raise LiumError(f'No Lium API key. Set {API_KEY_ENV_VAR} or write '
                        f'{CREDENTIAL_PATH}.')
    base_url = os.environ.get('LIUM_BASE_URL', DEFAULT_BASE_URL)
    response = requests.request(method,
                                f'{base_url}/{path.lstrip("/")}',
                                headers={
                                    'X-API-KEY': api_key,
                                    'X-Source': 'skypilot',
                                },
                                params=params,
                                json=json,
                                timeout=_REQUEST_TIMEOUT)
    if not response.ok:
        raise LiumError(f'Lium API error {response.status_code} on '
                        f'{method} {path}: {response.text}')
    return response.json()


def accelerator_name(gpu_model: str) -> Optional[str]:
    """Returns the SkyPilot accelerator name for a Lium GPU model."""
    name = gpu_model
    for prefix in ('NVIDIA ', 'GeForce '):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return GPU_NAME_MAP.get(name)


def make_instance_type(acc_name: str, acc_count: int) -> str:
    """Builds the instance type name for an accelerator and a GPU count."""
    return f'{acc_name}_{acc_count}x'


def parse_instance_type(instance_type: str) -> Tuple[str, int]:
    """Splits an instance type back into accelerator name and GPU count."""
    acc_name, _, count_part = instance_type.rpartition('_')
    if not acc_name or not count_part.endswith('x'):
        raise ValueError(f'Malformed Lium instance type: {instance_type}')
    return acc_name, int(count_part[:-1])


def _parse_node(payload: Dict[str, Any]) -> Optional[LiumNode]:
    """Builds a node out of an /executors row, or None if it has no GPU."""
    gpu = payload.get('specs', {}).get('gpu', {})
    details = gpu.get('details', [])
    if not details:
        return None
    gpu_count = gpu.get('count', len(details))
    return LiumNode(
        id=payload['id'],
        gpu_model=details[0].get('name', ''),
        gpu_count=gpu_count,
        driver_version=gpu.get('driver', ''),
        price_per_hour=(payload.get('price_per_gpu') or 0) * gpu_count,
        country_code=(payload.get('location') or {}).get('country_code', ''),
        tier=payload.get('tier', ''),
        is_whole_host_free=bool(payload.get('is_whole_host_free')))


def find_cheapest_free_node(instance_type: str,
                            region: str) -> Optional[LiumNode]:
    """Returns the cheapest free node that matches the instance type.

    Lium rents a node whole, so the node must offer exactly the number of GPUs
    that the instance type asks for.
    """
    acc_name, acc_count = parse_instance_type(instance_type)
    payloads = _request('GET',
                        '/executors',
                        params={
                            'size': 1000,
                            'gpu_count_gte': acc_count,
                            'gpu_count_lte': acc_count,
                        })
    candidates: List[LiumNode] = []
    for payload in payloads:
        node = _parse_node(payload)
        if node is None:
            continue
        if accelerator_name(node.gpu_model) != acc_name:
            continue
        if node.country_code != region:
            continue
        # A spot node can be taken back at any time, and SkyPilot rents from
        # Lium as on-demand. A node that already runs a pod cannot be rented
        # whole, which is the shape the catalog quotes.
        if node.tier != SECURE_TIER or not node.is_whole_host_free:
            continue
        candidates.append(node)
    if not candidates:
        return None
    return min(candidates, key=lambda node: node.price_per_hour)


def _default_template_id(node: LiumNode) -> str:
    """Returns the template that Lium runs on a node by default.

    Lium starts a pod from a template, which pins the image the node pulls.
    The default images for a GPU model come from the API; the first one that
    Lium also publishes as a template is the one to run.
    """
    images = _request('GET',
                      '/executors/default-docker-image',
                      params={
                          'gpu_model': node.gpu_model,
                          'driver_version': node.driver_version,
                      })
    templates = _request('GET', '/templates')
    templates_by_image = {(t.get('docker_image'), t.get('docker_image_tag')):
                          t['id'] for t in templates}
    for image in images:
        template_id = templates_by_image.get(
            (image.get('docker_image'), image.get('docker_image_tag')))
        if template_id is not None:
            return template_id
    raise LiumError(f'Lium has no template for {node.gpu_model} with driver '
                    f'{node.driver_version}.')


def rent_node(node: LiumNode, pod_name: str, public_key: str) -> str:
    """Rents a node and returns the id of the pod that runs on it."""
    pod = _request('POST',
                   f'/executors/{node.id}/rent',
                   json={
                       'pod_name': pod_name,
                       'template_id': _default_template_id(node),
                       'user_public_key': [public_key],
                   })
    return pod['id']


def _parse_pod(payload: Dict[str, Any]) -> LiumPod:
    """Builds a pod out of a /pods row.

    The API reports the SSH endpoint as a command line, so the host and the
    port come out of that string.
    """
    ssh_command = payload.get('ssh_connect_cmd') or ''
    host_match = re.search(r'@(\S+)', ssh_command)
    port_match = re.search(r'-p\s+(\d+)', ssh_command)
    return LiumPod(id=payload['id'],
                   name=payload.get('pod_name', ''),
                   status=payload.get('status', 'unknown'),
                   host=host_match.group(1) if host_match else None,
                   ssh_port=int(port_match.group(1)) if port_match else 22)


def get_cluster_pods(cluster_name_on_cloud: str) -> Dict[str, LiumPod]:
    """Returns the pods of a cluster, keyed by pod id."""
    prefix = f'{cluster_name_on_cloud}-'
    pods = [_parse_pod(payload) for payload in _request('GET', '/pods')]
    return {pod.id: pod for pod in pods if pod.name.startswith(prefix)}


def terminate_pod(pod_id: str) -> None:
    """Deletes a pod."""
    _request('DELETE', f'/pods/{pod_id}')


def _is_failed_status(status: str) -> bool:
    """Tells whether a pod status is one the pod cannot leave."""
    return status in POD_STATUS_MAP and POD_STATUS_MAP[status] is None


def wait_pod_ready(pod_id: str, timeout: int) -> Optional[LiumPod]:
    """Waits until a pod runs and reports its SSH endpoint.

    Returns None when the timeout expires. Raises when the pod fails or is
    gone, so a rent that broke does not hold the launch for the full timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        pod = _parse_pod(_request('GET', f'/pods/{pod_id}'))
        status = pod.status.upper()
        if _is_failed_status(status):
            raise LiumError(f'Pod {pod_id} failed to start: {status}.')
        if status == 'RUNNING' and pod.host is not None:
            return pod
        time.sleep(_POD_POLL_INTERVAL)
    return None

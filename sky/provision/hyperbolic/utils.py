"""Hyperbolic API utilities.

Uses the Hyperbolic v2 on-demand rentals API:
https://api.hyperbolic.ai/v2/openapi.json
"""
import enum
import os
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from sky import sky_logging
from sky.utils import auth_utils
from sky.utils import status_lib

BASE_URL = 'https://api.hyperbolic.ai'
API_KEY_PATH = '~/.hyperbolic/api_key'
RENTAL_TYPE = 'virtual-machine'

REQUEST_TIMEOUT = 120
_PAGE_SIZE = 100
# Backoff of 5, 10, 20, 30, ... seconds: about 3.5 minutes in total.
_MAX_RATE_LIMIT_RETRIES = 9
_RATE_LIMIT_BACKOFF_SECONDS = 5

# SkyPilot accelerator name -> Hyperbolic `gpuType`.
ACCELERATOR_TO_GPU_TYPE = {
    'H100': 'h100',
    'H200': 'h200',
    'B200': 'b200',
    'RTXPRO6000': 'rtx-6000-pro',
}

logger = sky_logging.init_logger(__name__)


class HyperbolicError(Exception):
    """Base exception for Hyperbolic API errors."""
    pass


class HyperbolicInstanceStatus(enum.Enum):
    """Statuses of a Hyperbolic rental."""
    PENDING = 'Pending'
    RUNNING = 'Running'
    REBOOTING = 'Rebooting'
    FAILED = 'Failed'
    TERMINATING = 'Terminating'
    TERMINATED = 'Terminated'
    UNKNOWN = 'Unknown'

    @classmethod
    def from_raw_status(cls,
                        status: Optional[str]) -> 'HyperbolicInstanceStatus':
        try:
            return cls(status or cls.UNKNOWN.value)
        except ValueError:
            return cls.UNKNOWN

    def to_cluster_status(self) -> Optional[status_lib.ClusterStatus]:
        """Convert to SkyPilot cluster status."""
        if self == HyperbolicInstanceStatus.RUNNING:
            return status_lib.ClusterStatus.UP
        if self in (HyperbolicInstanceStatus.TERMINATING,
                    HyperbolicInstanceStatus.TERMINATED):
            return None
        return status_lib.ClusterStatus.INIT


class HyperbolicClient:
    """Client for interacting with the Hyperbolic API."""

    def __init__(self):
        cred_path = os.path.expanduser(API_KEY_PATH)
        if not os.path.exists(cred_path):
            raise RuntimeError(f'API key not found at {cred_path}')
        with open(cred_path, 'r', encoding='utf-8') as f:
            api_key = f.read().strip()
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

    def _make_request(self,
                      method: str,
                      endpoint: str,
                      payload: Optional[Dict[str, Any]] = None,
                      params: Optional[Dict[str, Any]] = None) -> Any:
        """Make an API request to Hyperbolic and return the JSON body."""
        url = f'{BASE_URL}{endpoint}'
        logger.debug(f'Hyperbolic {method} {url} params={params} '
                     f'payload={payload}')
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = requests.request(method,
                                            url,
                                            headers=self.headers,
                                            json=payload,
                                            params=params,
                                            timeout=REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as e:
                raise HyperbolicError(
                    f'Request to {endpoint} failed: {e}') from e
            logger.debug(f'Hyperbolic response {response.status_code}: '
                         f'{response.text}')
            # The API rate-limits per key and the limit can last about a
            # minute. A 429 means the request was rejected, so retrying is
            # safe even for non-idempotent requests.
            if (response.status_code != 429 or
                    attempt == _MAX_RATE_LIMIT_RETRIES):
                break
            delay = min(_RATE_LIMIT_BACKOFF_SECONDS * 2**attempt, 30)
            logger.debug(f'Hyperbolic rate limit hit; retrying in {delay}s.')
            time.sleep(delay)
        if not response.ok:
            try:
                body = response.json()
                message = body.get('message', response.text)
                issues = '; '.join(
                    i.get('message', '') for i in body.get('issues') or [])
                if issues:
                    message = f'{message} ({issues})'
            except (ValueError, AttributeError):
                message = response.text
            raise HyperbolicError(f'{method} {endpoint} failed with status '
                                  f'{response.status_code}: {message}')
        if not response.content:
            return None
        return response.json()

    def _get_ssh_key_id(self) -> int:
        """Returns the ID of SkyPilot's SSH key, registering it if needed."""
        _, public_key_path = auth_utils.get_or_generate_keys()
        with open(public_key_path, 'r', encoding='utf-8') as f:
            public_key = f.read().strip()
        # Compare key type and body only; the trailing comment may differ.
        key_body = ' '.join(public_key.split()[:2])
        for key in self._make_request('GET', '/v2/ssh-keys'):
            if ' '.join(key['publicKey'].split()[:2]) == key_body:
                return key['id']
        key = self._make_request('POST',
                                 '/v2/ssh-keys',
                                 payload={
                                     'publicKey': public_key,
                                     'name': 'skypilot'
                                 })
        return key['id']

    def launch_instance(self, gpu_type: str, gpu_count: int, region: str,
                        name: str) -> str:
        """Creates a virtual machine rental and returns its rental ID."""
        payload = {
            'rentalType': RENTAL_TYPE,
            'gpuType': gpu_type,
            'gpuCount': gpu_count,
            'region': region,
            'label': name,
            'sshPublicKeyIds': [self._get_ssh_key_id()],
        }
        rental = self._make_request('POST',
                                    '/v2/on-demand/rentals',
                                    payload=payload)
        rental_id = rental.get('rentalId')
        if not rental_id:
            raise HyperbolicError(f'No rental ID returned from API: {rental}')
        logger.info(f'Created Hyperbolic rental {rental_id} in {region}.')
        return rental_id

    def list_instances(self, cluster_name: str) -> Dict[str, Dict[str, Any]]:
        """Lists active VM rentals labeled with the cluster name."""
        rentals: List[Dict[str, Any]] = []
        page = 1
        while True:
            response = self._make_request('GET',
                                          '/v2/on-demand/rentals/active',
                                          params={
                                              'rentalType': RENTAL_TYPE,
                                              'page': page,
                                              'pageSize': _PAGE_SIZE,
                                          })
            rentals.extend(response.get('items', []))
            if page >= response.get('totalPages', 1):
                break
            page += 1
        return {
            r['rentalId']: r for r in rentals if r.get('label') == cluster_name
        }

    def terminate_instance(self, rental_id: str) -> None:
        """Terminates a rental by ID."""
        self._make_request('POST',
                           '/v2/on-demand/rentals/terminate',
                           payload={'rentalId': rental_id})


_client: Optional[HyperbolicClient] = None


def get_client() -> HyperbolicClient:
    """Get or create the Hyperbolic client singleton."""
    global _client
    if _client is None:
        _client = HyperbolicClient()
    return _client


def get_status(rental: Dict[str, Any]) -> HyperbolicInstanceStatus:
    return HyperbolicInstanceStatus.from_raw_status(rental.get('status'))


def launch_instance(gpu_type: str, gpu_count: int, region: str,
                    name: str) -> str:
    return get_client().launch_instance(gpu_type, gpu_count, region, name)


def list_instances(cluster_name: str) -> Dict[str, Dict[str, Any]]:
    return get_client().list_instances(cluster_name)


def terminate_instance(rental_id: str) -> None:
    get_client().terminate_instance(rental_id)


def wait_for_instances(cluster_name: str,
                       done: Callable[[Dict[str, Dict[str, Any]]], bool],
                       timeout: int,
                       poll_interval: int = 5) -> Dict[str, Dict[str, Any]]:
    """Polls the cluster's rentals until done(rentals) is true."""
    start = time.time()
    while True:
        rentals = list_instances(cluster_name)
        if done(rentals):
            return rentals
        if time.time() - start > timeout:
            raise TimeoutError(
                f'Timed out after {timeout}s waiting for Hyperbolic rentals of '
                f'cluster {cluster_name}: '
                f'{ {k: v.get("status") for k, v in rentals.items()} }')
        time.sleep(poll_interval)

"""io.net VMaaS API client and deployment metadata."""

import json
import os
import re
import time
import typing
from typing import Any, Dict, Optional, Tuple

from sky.adaptors import common as adaptors_common
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

API_KEY_PATH = '~/.ionet/api_key'
METADATA_DIR = '~/.ionet'
API_BASE_URL = 'https://api.io.solutions'
API_PREFIX = '/enterprise/v1/io-cloud/vmaas'
MAX_ATTEMPTS = 4
REQUEST_TIMEOUT = 30
# Page size for list endpoints, and the page cap that bounds a scan.
PAGE_SIZE = 100
MAX_PAGES = 100


class IONetError(Exception):
    """An io.net API or configuration error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        # HTTP status of a non-2xx API response; None for other errors.
        self.status_code = status_code


def get_api_key() -> str:
    api_key = os.environ.get('IONET_API_KEY')
    if api_key:
        return api_key
    path = os.path.expanduser(API_KEY_PATH)
    try:
        with open(path, 'r', encoding='utf-8') as file:
            api_key = file.read().strip()
    except OSError as exc:
        raise IONetError(f'io.net API key not found at {path}.') from exc
    if not api_key:
        raise IONetError(f'io.net API key is empty in {path}.')
    return api_key


def parse_ssh_access(ssh_access: str) -> Tuple[str, str, int]:
    """Parse the VMaaS `ssh -p PORT user@host` connection string."""
    match = re.fullmatch(r'ssh\s+-p\s+(\d+)\s+([^\s@]+)@([^\s]+)',
                         ssh_access.strip())
    if match is None:
        raise IONetError(f'Invalid io.net ssh_access: {ssh_access!r}')
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        raise IONetError(f'Invalid io.net SSH port: {port}')
    return match.group(2), match.group(3), port


class IONetMetadata:
    """Local record of the deployments SkyPilot created for a cluster.

    Discovery lists deployments by name remotely; this record is a fallback
    so that a paid deployment the list API omits is still found, reported
    and terminated instead of leaked.
    """

    def __init__(self, cluster_name: str):
        self.path = os.path.join(os.path.expanduser(METADATA_DIR),
                                 f'deployments-{cluster_name}.json')
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def list_deployments(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, 'r', encoding='utf-8') as file:
            return json.load(file)

    def set_deployment(self, deployment_id: str,
                       deployment_info: Optional[Dict[str, Any]]) -> None:
        deployments = self.list_deployments()
        if deployment_info is None:
            deployments.pop(deployment_id, None)
        else:
            deployments[deployment_id] = deployment_info
        if not deployments:
            if os.path.exists(self.path):
                os.remove(self.path)
            return
        temp_path = self.path + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as file:
            json.dump(deployments, file)
        os.replace(temp_path, self.path)


class IONetClient:
    """Small client for the enterprise VMaaS endpoints."""

    def __init__(self, api_key: Optional[str] = None):
        self.headers = {
            'x-api-key': api_key or get_api_key(),
            'Content-Type': 'application/json'
        }

    def _request(self,
                 method: str,
                 path: str,
                 *,
                 json_data: Optional[Dict[str, Any]] = None,
                 params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = API_BASE_URL + API_PREFIX + path
        # Retrying a paid deploy can create duplicate deployments.
        attempts = MAX_ATTEMPTS if method == 'GET' else 1
        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=4)
        for attempt in range(attempts):
            try:
                response = requests.request(method,
                                            url,
                                            headers=self.headers,
                                            json=json_data,
                                            params=params,
                                            timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                if attempt == attempts - 1:
                    raise IONetError(
                        f'io.net {method} request failed: {exc}') from exc
                time.sleep(backoff.current_backoff())
                continue
            if response.status_code == 429 and attempt < attempts - 1:
                time.sleep(backoff.current_backoff())
                continue
            if not response.ok:
                try:
                    detail = response.json().get('detail', response.reason)
                except (ValueError, AttributeError):
                    detail = response.reason
                raise IONetError(
                    f'io.net API HTTP {response.status_code}: {detail}',
                    status_code=response.status_code)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise IONetError('io.net API returned invalid JSON.') from exc
        raise IONetError('io.net API request exhausted retries.')

    def deploy_vms(self, deploy_request: Dict[str, Any]) -> Dict[str, Any]:
        return self._request('POST', '/deploy', json_data=deploy_request)

    def get_deployment_details(self, deployment_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/deployment/{deployment_id}')

    def get_deployment_vms(self,
                           deployment_id: str,
                           page: int = 1) -> Dict[str, Any]:
        return self._request('GET',
                             f'/deployment/{deployment_id}/vms',
                             params={
                                 'page': page,
                                 'page_size': PAGE_SIZE
                             })

    def destroy_deployment(self, deployment_id: str) -> Dict[str, Any]:
        return self._request('DELETE', f'/deployment/{deployment_id}')

    def list_deployments(self, page: int = 1) -> Dict[str, Any]:
        return self._request('GET',
                             '/deployments',
                             params={
                                 'page': page,
                                 'page_size': PAGE_SIZE
                             })

    def get_hardware(self) -> Dict[str, Any]:
        return self._request('GET', '/hardware')

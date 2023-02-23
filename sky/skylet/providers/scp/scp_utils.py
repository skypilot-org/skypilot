"""SCP Cloud helper functions."""
import os
import json
import requests
import time
from datetime import datetime, timedelta
import hashlib
import hmac
import base64
from typing import Any, Dict, List

CREDENTIALS_PATH = '~/.scp/scp_credential'
API_ENDPOINT = 'https://openapi.samsungsdscloud.com/virtual-server'
TEMP_VM_JSON_PATH = '/tmp/json/tmp_vm_body.json'


class SCPError(Exception):
    pass


class Metadata:
    """Per-cluster metadata file."""

    def __init__(self, path_prefix: str, cluster_name: str) -> None:
        # TODO(ewzeng): Metadata file is not thread safe. This is fine for
        # now since SkyPilot uses a per-cluster lock for ray-related
        # operations. In the future, add a filelock around __getitem__,
        # __setitem__ and refresh.
        self.path = os.path.expanduser(f'{path_prefix}-{cluster_name}')
        # In case parent directory does not exist
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def __getitem__(self, instance_id: str) -> Dict[str, Any]:
        assert os.path.exists(self.path), 'Metadata file not found'
        with open(self.path, 'r') as f:
            metadata = json.load(f)
        return metadata.get(instance_id)

    def __setitem__(self, instance_id: str, value: Dict[str, Any]) -> None:
        # Read from metadata file
        if os.path.exists(self.path):
            with open(self.path, 'r') as f:
                metadata = json.load(f)
        else:
            metadata = {}
        # Update metadata
        if value is None:
            if instance_id in metadata:
                metadata.pop(instance_id) # del entry
            if len(metadata) == 0:
                if os.path.exists(self.path):
                    os.remove(self.path)
                return
        else:
            metadata[instance_id] = value
        # Write to metadata file
        with open(self.path, 'w') as f:
            json.dump(metadata, f)

    def refresh(self, instance_ids: List[str]) -> None:
        """Remove all tags for instances not in instance_ids."""
        if not os.path.exists(self.path):
            return
        with open(self.path, 'r') as f:
            metadata = json.load(f)
        for instance_id in list(metadata.keys()):
            if instance_id not in instance_ids:
                del metadata[instance_id]
        if len(metadata) == 0:
            os.remove(self.path)
            return
        with open(self.path, 'w') as f:
            json.dump(metadata, f)


def raise_scp_error(response: requests.Response) -> None:
    """Raise SCPCloudError if appropriate. """
    status_code = response.status_code
    if status_code == 200 or status_code == 202 :
        return
    if status_code == 429:
        # https://docs.lambdalabs.com/cloud/rate-limiting/
        raise SCPError('Your API requests are being rate limited.')
    try:
        resp_json = response.json()
        code = resp_json['error']['code']
        message = resp_json['error']['message']
    except (KeyError, json.decoder.JSONDecodeError):
        raise SCPError(f'Unexpected error. Status code: {status_code}')
    raise SCPError(f'{code}: {message}')


class SCPClient:
    """Wrapper functions for SCP Cloud API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r') as f:
            lines = [line.strip() for line in f.readlines() if ' = ' in line]
            self._credentials = {
                line.split(' = ')[0]: line.split(' = ')[1]
                for line in lines
            }
        self.access_key = self._credentials['scp_access_key']
        self.secret_key = self._credentials.get('scp_secret_key', None)
        self.client_type = self._credentials.get('scp_client_type', None)
        self.project_id = self._credentials.get('scp_project_id', None)
        self.timestamp = self._credentials.get('scp_timestamp', None)
        self.signature = self._credentials.get('scp_signature', None)

        self.headers = {
            'X-Cmp-AccessKey': f'{self.access_key}',
            'X-Cmp-ClientType': f'{self.client_type}',
            'X-Cmp-ProjectId': f'{self.project_id}',
            'X-Cmp-Timestamp': f'{self.timestamp}',
            'X-Cmp-Signature': f'{self.signature}'
         }

    def create_instances(self, server_name=None):
        """Launch new instances."""
        # assert self.ssh_key_name is not None

        # Optimization:
        # Most API requests are rate limited at ~1 request every second but
        # launch requests are rate limited at ~1 request every 10 seconds.
        # So don't use launch requests to check availability.
        # See https://docs.lambdalabs.com/cloud/rate-limiting/ for more.
        # available_regions = self.list_catalog()[instance_type]\
        #         ['regions_with_capacity_available']
        # available_regions = [reg['name'] for reg in available_regions]
        # if region not in available_regions:
        #     if len(available_regions) > 0:
        #         aval_reg = ' '.join(available_regions)
        #     else:
        #         aval_reg = 'None'
        #     raise SCPCloudError(('instance-operations/launch/'
        #                            'insufficient-capacity: Not enough '
        #                            'capacity to fulfill launch request. '
        #                            'Regions with capacity available: '
        #                            f'{aval_reg}'))

        url = f'{API_ENDPOINT}/v2/virtual-servers'
        method = 'POST'

        self.set_timestamp()
        self.set_signature(url=url, method=method)

        # f = open('./test.json', )
        f = open(TEMP_VM_JSON_PATH, )
        data = json.load(f)

        if server_name:
            data['virtualServerName'] = server_name
        response = requests.post(f'{API_ENDPOINT}/v2/virtual-servers',
                                 json=data,
                                 headers=self.headers)
        raise_scp_error(response)
        return response.json()

    def remove_instances(self, *instance_ids: str) -> Dict[str, Any]:
        """Terminate instances."""
        data = json.dumps({
            'instance_ids': [
                instance_ids[0] # TODO(ewzeng) don't hardcode
            ]
        })
        response = requests.post(f'{API_ENDPOINT}/instance-operations/terminate',
                                 data=data,
                                 headers=self.headers)
        raise_scp_error(response)
        return response.json().get('data', []).get('terminated_instances', [])

    def list_instances(self) -> List[dict]:
        """List existing instances."""
        url = f'{API_ENDPOINT}/v2/virtual-servers'
        method = 'GET'

        self.set_timestamp()
        self.set_signature(url=url, method=method)

        response = requests.get(url, headers=self.headers)
        raise_scp_error(response)
        return response.json().get('contents', [])

    def set_ssh_key(self, name: str, pub_key: str) -> None:
        """Set ssh key."""
        data = json.dumps({
            'name': name,
            'public_key': pub_key
        })
        response = requests.post(f'{API_ENDPOINT}/ssh-keys',
                                 data=data,
                                 headers=self.headers)
        raise_scp_error(response)
        self.ssh_key_name = name
        with open(self.credentials, 'w') as f:
            f.write(f'api_key = {self.api_key}\n')
            f.write(f'ssh_key_name = {self.ssh_key_name}\n')

    def list_catalog(self) -> Dict[str, Any]:
        """List offered instances and their availability."""
        response = requests.get(f'{API_ENDPOINT}/instance-types',
                                headers=self.headers)
        raise_scp_error(response)
        return response.json().get('data', [])

    def get_signature(self, method:str, url:str) -> str:
        message = method + url + self.timestamp + self.access_key + self.project_id + self.client_type

        message = bytes(message, 'utf-8')
        secret = bytes(self.secret_key, 'utf-8')

        signature = str(base64.b64encode(hmac.new(secret, message, digestmod=hashlib.sha256).digest()), 'utf-8')

        return str(signature)

    def set_timestamp(self) -> None:
        self.timestamp = str(int(round(datetime.timestamp(datetime.now() - timedelta(minutes=1)) * 1000)))
        self.headers['X-Cmp-Timestamp'] = self.timestamp

    def set_signature(self, method:str, url:str) -> None:
        self.signature = self.get_signature(url=url, method=method)
        self.headers['X-Cmp-Signature'] = self.signature


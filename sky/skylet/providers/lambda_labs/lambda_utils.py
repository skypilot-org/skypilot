"""Lambda Labs helper functions."""
import os
import json
import requests

CREDENTIALS_PATH = '~/.lambda_labs/lambda_keys'
API_ENDPOINT = 'https://cloud.lambdalabs.com/api/v1'


class LambdaLabsError(Exception):
    __module__ = 'builtins'


class Metadata:
    """Per-cluster metadata file."""

    def __init__(self, path_prefix, cluster_name):
        # TODO(ewzeng): Metadata file is not thread safe. This is fine for
        # now since SkyPilot uses a per-cluster lock for ray-related
        # operations, but this should be improved in the future.
        self._metadata_path = os.path.expanduser(
                f'{path_prefix}-{cluster_name}')
        self._metadata = {}
        if os.path.exists(self._metadata_path):
            with open(self._metadata_path, 'r') as f:
                self._metadata = json.load(f)
        else:
            # In case parent directory does not exist
            os.makedirs(os.path.dirname(self._metadata_path), exist_ok=True)

    def __getitem__(self, instance_id):
        return self._metadata.get(instance_id)

    def __setitem__(self, instance_id, value):
        if value is None:
            if instance_id in self._metadata:
                self._metadata.pop(instance_id) # del entry
            if (len(self._metadata) == 0 and
                    os.path.exists(self._metadata_path)):
                os.remove(self._metadata_path)
                return
        else:
            self._metadata[instance_id] = value
        with open(self._metadata_path, 'w') as f:
            json.dump(self._metadata, f)


def raise_lambda_error(response):
    """Raise LambdaLabsError if appropriate. """
    status_code = response.status_code
    if status_code == 200:
        return
    if status_code == 429:
        # https://docs.lambdalabs.com/cloud/rate-limiting/
        raise LambdaLabsError('Your API requests are being rate limited.')
    try:
        resp_json = response.json()
        code = resp_json['error']['code']
        message = resp_json['error']['message']
    except (KeyError, json.decoder.JSONDecodeError):
        raise LambdaLabsError(f'Unexpected error. Status code: {status_code}')
    raise LambdaLabsError(f'{code}: {message}')


class LambdaLabsClient:
    """Wrapper functions for Lambda Labs API."""

    def __init__(self):
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r') as f:
            lines = [line.strip() for line in f.readlines() if ' = ' in line]
            self._credentials = {
                line.split(' = ')[0]: line.split(' = ')[1]
                for line in lines
            }
        self.api_key = self._credentials['api_key']
        self.ssh_key_name = self._credentials.get('ssh_key_name', None)
        self.headers = {'Authorization': f'Bearer {self.api_key}'}

    def up(self,
           instance_type='gpu_1x_a100_sxm4',
           region='us-east-1',
           quantity=1,
           name=''):
        """Start a new instance."""
        assert self.ssh_key_name is not None

        # Optimization:
        # Most API requests are rate limited at ~1 request every second but
        # launch requests are rate limited at ~1 request every 10 seconds.
        # So don't use launch requests to check availability.
        # See https://docs.lambdalabs.com/cloud/rate-limiting/ for more.
        available_regions = self.ls_catalog()['data'][instance_type]\
                ['regions_with_capacity_available']
        available_regions = [reg['name'] for reg in available_regions]
        if region not in available_regions:
            if len(available_regions) > 0:
                aval_reg = ' '.join(available_regions)
            else:
                aval_reg = 'None'
            raise LambdaLabsError(('instance-operations/launch/'
                                   'insufficient-capacity: Not enough '
                                   'capacity to fulfill launch request. '
                                   'Regions with capacity available: '
                                   f'{aval_reg}'))

        # Try to launch instance
        data = json.dumps({
                    'region_name': region,
                    'instance_type_name': instance_type,
                    'ssh_key_names': [
                        self.ssh_key_name
                    ],
                    'quantity': quantity,
                    'name': name
                })
        response = requests.post(f'{API_ENDPOINT}/instance-operations/launch',
                                 data=data,
                                 headers=self.headers)
        raise_lambda_error(response)
        return response.json()

    def rm(self, *instance_ids):
        """Terminate instances."""
        data = json.dumps({
            'instance_ids': [
                instance_ids[0] # TODO(ewzeng) don't hardcode
            ]
        })
        response = requests.post(f'{API_ENDPOINT}/instance-operations/terminate',
                                 data=data,
                                 headers=self.headers)
        raise_lambda_error(response)
        return response.json()

    def ls(self):
        """List existing instances."""
        response = requests.get(f'{API_ENDPOINT}/instances', headers=self.headers)
        raise_lambda_error(response)
        return response.json()

    def set_ssh_key(self, name, pub_key):
        """Set ssh key."""
        data = json.dumps({
            'name': name,
            'public_key': pub_key
        })
        response = requests.post(f'{API_ENDPOINT}/ssh-keys',
                                 data=data,
                                 headers=self.headers)
        raise_lambda_error(response)
        self.ssh_key_name = name
        with open(self.credentials, 'w') as f:
            f.write(f'api_key={self.api_key}\n')
            f.write(f'ssh_key_name={self.ssh_key_name}\n')

    def ls_catalog(self):
        """List offered instances and their availability."""
        response = requests.get(f'{API_ENDPOINT}/instance-types',
                                headers=self.headers)
        raise_lambda_error(response)
        return response.json()

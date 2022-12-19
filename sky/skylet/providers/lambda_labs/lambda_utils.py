"""Lambda Labs helper functions."""
import os
import json
import pathlib
import colorama
import requests

Fore = colorama.Fore
Style = colorama.Style

CREDENTIALS_PATH = '~/.lambda/lambda_keys'
LOCAL_METADATA_PATH = '~/.lambda/metadata'
API_ENDPOINT = 'https://cloud.lambdalabs.com/api/v1'
here = pathlib.Path(os.path.abspath(os.path.dirname(__file__)))

class LambdaError(Exception):
    __module__ = 'builtins'


class Metadata:
    """Local metadata for a Lambda Labs instance."""

    def __init__(self):
        self._metadata_path = os.path.expanduser(LOCAL_METADATA_PATH)
        self._metadata = {}
        if os.path.exists(self._metadata_path):
            with open(self._metadata_path, 'r') as f:
                self._metadata = json.load(f)

    def __getitem__(self, instance_id):
        return self._metadata.get(instance_id)

    def __setitem__(self, instance_id, value):
        self._metadata[instance_id] = value
        with open(self._metadata_path, 'w') as f:
            json.dump(self._metadata, f)


def raise_lambda_error(response, error_status_codes):
    """Raise LambdaError if appropriate.

    response: Lambda Labs API response
    error_status_codes: if response.status_code is in error_status_codes, then
                        an error has occurred but response.json still exists
    """
    if response.status_code == 200:
        return
    if response.status_code in error_status_codes:
        resp_json = response.json()
        code = resp_json['error']['code']
        message = resp_json['error']['message']
        raise LambdaError(f'{code}: {message}')
    else:
        # Error, but no json
        raise LambdaError('Unexpected error.')


class Lambda:
    """Wrapper functions for Lambda Labs API."""

    def __init__(self):
        credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(credentials), 'Credentials not found'
        with open(credentials, 'r') as f:
            lines = [line.strip() for line in f.readlines() if '=' in line]
            self._credentials = {
                line.split('=')[0]: line.split('=')[1]
                for line in lines
            }
        self.api_key = self._credentials['api_key']
        self.ssh_key_name = self._credentials['ssh_key_name']
        self.headers = {'Authorization': f'Bearer {self.api_key}'}


    def up(self,
           instance_type='gpu_1x_a100_sxm4',
           region='us-tx-1',
           quantity=1,
           name=''):
        """Start a new instance."""
        assert instance_type=='gpu_1x_a100_sxm4', instance_type
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
        raise_lambda_error(response, [400, 401, 403, 404, 500])
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
        raise_lambda_error(response, [400, 401, 403, 404, 500])
        return response.json()

    def ls(self):
        """List existing instances."""
        response = requests.get(f'{API_ENDPOINT}/instances', headers=self.headers)
        raise_lambda_error(response, [400, 401, 403, 404])
        return response.json()

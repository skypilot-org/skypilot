"""Yotta API client."""

import base64
import enum
import json
import os
from typing import Any, Dict, List, Optional, Tuple
import uuid

import requests

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

CREDENTIALS_FILE_PATH = os.path.expanduser('~/.yotta/credentials')
# ENDPOINT = "https://api.yottalabs.ai/sdk/api"
ENDPOINT = 'https://api.dev.yottalabs.ai/openapi'
API_KEY_HEADER = 'X-API-KEY'


class PodStatusEnum(enum.Enum):
    """Pod status."""
    INITIALIZE = 'INITIALIZE'
    RUNNING = 'RUNNING'
    PAUSING = 'PAUSING'
    PAUSED = 'PAUSED'
    TERMINATING = 'TERMINATING'
    TERMINATED = 'TERMINATED'
    FAILED = 'FAILED'


class ClusterStatusEnum(enum.Enum):
    """Cluster status."""
    INITIALIZE = 'INITIALIZE'
    RUNNING = 'RUNNING'
    TERMINATING = 'TERMINATING'
    TERMINATED = 'TERMINATED'


class CloudType(enum.Enum):
    """cloud type."""
    SECURE = 1
    COMMUNITY = 2


class ClusterTypeEnum(enum.Enum):
    """Cluster type."""
    PRIVATE = 1
    PUBLIC = 2


class ClusterSourceEnum(enum.Enum):
    """Cluster source."""
    SKY_PILOT = 1


def get_key_suffix():
    return str(uuid.uuid4()).replace('-', '')[:8]


def _load_credentials() -> Tuple[str, str]:
    """Reads the credentials file and returns userId and apikey."""
    if not os.path.isfile(CREDENTIALS_FILE_PATH):
        raise FileNotFoundError(
            f'Credentials file not found at {CREDENTIALS_FILE_PATH}')

    try:
        with open(CREDENTIALS_FILE_PATH, 'r') as f:
            credentials = {}
            for line in f:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    credentials[key] = value

        org_id: str = credentials.get('orgId', '')
        api_key: str = credentials.get('apikey', '')

        if not org_id or not api_key:
            raise ValueError(
                f'Missing orgId or apikey in credentials'
                f' file: {CREDENTIALS_FILE_PATH}. '
                'Please ensure the file contains \'orgId=<your_org_id>\' and '
                '\'apikey=<your_api_key>\'.')

        return org_id, api_key
    except Exception as e:
        raise ValueError(
            f'Error reading credentials file: {CREDENTIALS_FILE_PATH}. {e}'
        ) from e


def get_ssh_port(instance):
    # get ssh port example:
    # {"port":22,"proxyPort":30003,"protocol":"SSH",
    # "host":"127.0.0.1","privateHost":"127.0.0.1","healthy":true}
    expose = instance.get('expose', [])
    for port in expose:
        if port.get('protocol') == 'SSH':
            return port
    return None


def raise_yotta_error(response: 'requests.Response') -> None:
    """Raise YottaAPIError if appropriate."""
    status_code = response.status_code
    logger.debug(f'response: {response.status_code} - {response.text}')
    try:
        resp_json = response.json()
    except (KeyError, json.decoder.JSONDecodeError) as e:
        raise YottaAPIError(
            f'Unexpected error. Status code: {status_code} \n {response.text} '
            f'\n {str(e)}', status_code) from e
    if response.ok:
        if resp_json.get('code') != 10000:
            raise YottaAPIError(
                f'Business error: {resp_json.get("message", "Unknown error")}',
                resp_json.get('code', status_code))
        return
    else:
        raise YottaAPIError(
            f'Unexpected error. Status code: {status_code} \n {response.text}',
            status_code)


class YottaAPIError(Exception):

    def __init__(self, message: str, code: int = 400):
        self.code = code
        super().__init__(message)


class YottaClient:
    """Yotta API Client"""

    def __init__(self):
        self.org_id, self.api_key = _load_credentials()

    def check_api_key(self) -> bool:
        url = f'{ENDPOINT}/key/check?orgId={self.org_id}'
        logger.debug(f'Checking api key for user {self.org_id}')
        response = requests.get(url, headers={API_KEY_HEADER: self.api_key})
        raise_yotta_error(response)
        check_result = response.json()
        # True if api key is valid
        logger.debug(f'Api key check result: {check_result}')
        return check_result['data']

    def list_instances(self,
                       cluster_name_on_cloud: str) -> Dict[str, Dict[str, Any]]:
        url = f'{ENDPOINT}/v1/pods/cluster/pods/list'
        all_records: List[Dict[str, Any]] = []
        request_data = {
            'clusterName': cluster_name_on_cloud,
            'source': ClusterSourceEnum.SKY_PILOT.value
        }
        logger.debug(f'Listing instances for cluster {cluster_name_on_cloud}')
        response = requests.post(url,
                                 headers={API_KEY_HEADER: self.api_key},
                                 json=request_data)
        response.raise_for_status()
        response_json = response.json()
        logger.debug(f'Listing instances for cluster {cluster_name_on_cloud}'
                     f' response: {response_json}')
        if response_json['code'] == 44003:
            logger.debug('Cluster not found return empty list')
            return {}
        if response_json['code'] != 10000:
            raise ValueError(
                f'API returned an error: {response_json["message"]}')

        records = response_json['data']
        all_records.extend(records)

        unique_records = {}
        for record in all_records:
            unique_records[record['id']] = record
            status = PodStatusEnum(record.get('status'))
            if status == PodStatusEnum.RUNNING:
                ports = record.get('expose', [])
                record['port2endpoint'] = {}
                for port in ports:
                    # container private port mapping to host public port
                    record['port2endpoint'][port['port']] = {
                        'host': port['host'],
                        'port': port['proxyPort']
                    }
        return unique_records

    def create_cluster(self, cluster_name: str, instance_type: str, region: str,
                       image_name: str, ports: Optional[List[int]],
                       public_key: str, ssh_user: str, node_num: int) -> str:
        url = f'{ENDPOINT}/v1/pods/cluster/create'
        expose = []
        if ports is not None:
            for p in ports:
                expose.append({'port': p, 'protocol': 'TCP'})
        expose.append({'port': 22, 'protocol': 'SSH'})
        expose.append({
            'port': constants.SKY_REMOTE_RAY_DASHBOARD_PORT,
            'protocol': 'HTTP'
        })
        expose.append({
            'port': constants.SKY_REMOTE_RAY_PORT,
            'protocol': 'HTTP'
        })

        request_data = {
            'clusterName': cluster_name,
            'instanceType': instance_type,
            'region': region,
            'imageName': image_name,
            'expose': expose,
            'publicKey': public_key,
            'sshUser': ssh_user,
            'nodeNum': node_num,
            'clusterType': ClusterTypeEnum.PRIVATE.value,
            'source': ClusterSourceEnum.SKY_PILOT.value,
        }
        response = requests.post(url,
                                 headers={API_KEY_HEADER: self.api_key},
                                 json=request_data)
        logger.debug(f'Creating cluster {cluster_name}, '
                     f'response: {response.json()}')
        raise_yotta_error(response)
        return response.json()['data']['clusterId']

    def get_cluster_status(self, cluster_id: str) -> str:
        url = f'{ENDPOINT}/v1/pods/cluster/status/{cluster_id}'
        response = requests.get(url, headers={API_KEY_HEADER: self.api_key})
        logger.debug(f'Getting cluster status for {cluster_id}, '
                     f'response: {response.json()}')
        raise_yotta_error(response)
        return response.json()['data']['status']

    def launch(self, cluster_name: str, cluster_id: str, name: str,
               image_name: str, docker_login_config: Optional[Dict[str, Any]],
               ports: Optional[List[int]], public_key: str) -> str:
        """Launches an instance with the given parameters."""
        url = f'{ENDPOINT}/v1/pods/cluster/create/pod'

        # TODO : keep this align with setups in
        # `provision.kuberunetes.instance.py`
        setup_cmd = (
            'prefix_cmd() '
            '{ if [ $(id -u) -ne 0 ]; then echo "sudo"; else echo ""; fi; }; '
            '$(prefix_cmd) apt update;'
            'export DEBIAN_FRONTEND=noninteractive;'
            '$(prefix_cmd) apt install openssh-server rsync curl patch -y;'
            '$(prefix_cmd) mkdir -p /var/run/sshd; '
            '$(prefix_cmd) '
            'sed -i "s/PermitRootLogin prohibit-password/PermitRootLogin yes/" '
            '/etc/ssh/sshd_config; '
            '$(prefix_cmd) sed '
            '"s@session\\s*required\\s*pam_loginuid.so@session optional '
            'pam_loginuid.so@g" -i /etc/pam.d/sshd; '
            'cd /etc/ssh/ && $(prefix_cmd) ssh-keygen -A; '
            '$(prefix_cmd) mkdir -p ~/.ssh; '
            '$(prefix_cmd) chown -R $(whoami) ~/.ssh;'
            '$(prefix_cmd) chmod 700 ~/.ssh; '
            f'$(prefix_cmd) echo "{public_key}" >> ~/.ssh/authorized_keys; '
            '$(prefix_cmd) chmod 644 ~/.ssh/authorized_keys; '
            '$(prefix_cmd) service ssh restart; '
            '$(prefix_cmd) export -p > ~/container_env_var.sh && '
            '$(prefix_cmd) '
            'mv ~/container_env_var.sh /etc/profile.d/container_env_var.sh; '
            '[ $(id -u) -eq 0 ] && echo alias sudo="" >> ~/.bashrc;'
            'sleep infinity')
        # Use base64 to deal with the tricky quoting
        # issues caused by runpod API.
        encoded = base64.b64encode(setup_cmd.encode('utf-8')).decode('utf-8')

        docker_args = (f'bash -c \'echo {encoded} | base64 --decode > init.sh; '
                       f'bash init.sh\'')

        expose = []
        if ports is not None:
            for p in ports:
                expose.append({'port': p, 'protocol': 'TCP'})
        expose.append({'port': 22, 'protocol': 'SSH'})
        expose.append({
            'port': constants.SKY_REMOTE_RAY_DASHBOARD_PORT,
            'protocol': 'HTTP'
        })
        expose.append({
            'port': constants.SKY_REMOTE_RAY_PORT,
            'protocol': 'HTTP'
        })

        request_data = {
            'name': name,
            'imagePublicType': 'PRIVATE' if docker_login_config else 'PUBLIC',
            'image': image_name,
            'clusterId': cluster_id,
            'clusterName': cluster_name,
            'expose': expose,
            'initializationCommand': docker_args,
        }
        if docker_login_config:
            request_data['imageRegistryUsername'] = str(
                docker_login_config.get('username'))
            request_data['imageRegistryToken'] = str(
                docker_login_config.get('password'))

        response = requests.post(url,
                                 headers={API_KEY_HEADER: self.api_key},
                                 json=request_data)
        logger.debug(f'Launching instance for {cluster_id}, '
                     f'request: {request_data}, '
                     f'response: {response.json()}')
        raise_yotta_error(response)
        return response.json()['data']

    def terminate_instances(self, cluster_name: str):
        """Terminate instances."""
        url = f'{ENDPOINT}/v1/pods/cluster/release'
        request_data = {'clusterName': cluster_name}
        response = requests.post(url=url,
                                 headers={API_KEY_HEADER: self.api_key},
                                 json=request_data)
        logger.debug(f'Terminating instances for {cluster_name}, '
                     f'response: {response.json()}')
        raise_yotta_error(response)
        return response.json()


yotta_client = YottaClient()

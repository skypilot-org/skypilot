"""SCP Open-API Functions.

This module contains a set of rest api functions accessing SCP Open-API.
"""
import base64
from functools import wraps
import hashlib
import hmac
import json
import logging
import os
import time
import typing
from typing import Any, Dict, List, Optional
from urllib import parse

from sky.adaptors import common as adaptors_common

if typing.TYPE_CHECKING:
    import requests
else:
    requests = adaptors_common.LazyImport('requests')

CREDENTIALS_PATH = '~/.scp/scp_credential'
API_ENDPOINT = 'https://openapi.samsungsdscloud.com'
TEMP_VM_JSON_PATH = '/tmp/json/tmp_vm_body.json'

logger = logging.getLogger(__name__)


class SCPClientError(Exception):
    pass


class SCPOngoingRequestError(Exception):
    pass


class SCPCreationFailError(Exception):
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
        with open(self.path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        return metadata.get(instance_id)

    def __setitem__(self, instance_id: str, value: Optional[Dict[str,
                                                                 Any]]) -> None:
        # Read from metadata file
        if os.path.exists(self.path):
            with open(self.path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        else:
            metadata = {}
        # Update metadata
        if value is None:
            if instance_id in metadata:
                metadata.pop(instance_id)  # del entry
            if not metadata:
                if os.path.exists(self.path):
                    os.remove(self.path)
                return
        else:
            metadata[instance_id] = value
        # Write to metadata file
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f)

    def refresh(self, instance_ids: List[str]) -> None:
        """Remove all tags for instances not in instance_ids."""
        if not os.path.exists(self.path):
            return
        with open(self.path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        for instance_id in list(metadata.keys()):
            if instance_id not in instance_ids:
                del metadata[instance_id]
        if not metadata:
            os.remove(self.path)
            return
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f)

    def keys(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
            return list(metadata.keys())


def raise_scp_error(response: 'requests.Response') -> None:
    """Raise SCPCloudError if appropriate. """
    status_code = response.status_code
    if status_code in (200, 202):
        return
    try:
        resp_json = response.json()
        message = resp_json['message']
    except (KeyError, json.decoder.JSONDecodeError) as e:
        raise SCPClientError('Unexpected error. Status code: '
                             f'{status_code}') from e

    if status_code == 404:
        raise SCPCreationFailError(f'{status_code}: {message}')

    if 'There is an ongoing request' in message:
        raise SCPOngoingRequestError(f'{status_code}: {message}')

    raise SCPClientError(f'{status_code}: {message}')


def _singleton(class_):
    instances = {}

    def get_instance(*args, **kwargs):
        if class_ not in instances:
            instances[class_] = class_(*args, **kwargs)
        return instances[class_]

    return get_instance


def _retry(method, max_tries=50, backoff_s=5):

    @wraps(method)
    def method_with_retries(self, *args, **kwargs):
        try_count = 0
        while try_count < max_tries:
            try:
                return method(self, *args, **kwargs)
            except SCPOngoingRequestError:
                logger.warning('Caught a Ongoing Request. Retrying.')
                try_count += 1
                if try_count < max_tries:
                    time.sleep(backoff_s)
                else:
                    raise

    return method_with_retries


@_singleton
class SCPClient:
    """Wrapper functions for SCP Cloud API."""

    def __init__(self) -> None:
        self.credentials = os.path.expanduser(CREDENTIALS_PATH)
        assert os.path.exists(self.credentials), 'Credentials not found'
        with open(self.credentials, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f.readlines() if ' = ' in line]
            self._credentials = {
                line.split(' = ')[0]: line.split(' = ')[1] for line in lines
            }
        self.access_key = self._credentials['access_key']
        self.secret_key = self._credentials['secret_key']
        self.project_id = self._credentials['project_id']
        self.client_type = 'OpenApi'

    def _signed_headers(self, method: str, url: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        signature = self.get_signature(method=method,
                                       url=url,
                                       timestamp=timestamp)
        return {
            'X-Cmp-AccessKey': self.access_key,
            'X-Cmp-ClientType': self.client_type,
            'X-Cmp-ProjectId': self.project_id,
            'X-Cmp-Timestamp': timestamp,
            'X-Cmp-Signature': signature,
        }

    def create_instance(self, instance_config):
        """Launch new instances."""
        url = f'{API_ENDPOINT}/virtual-server/v4/virtual-servers'
        return self._post(url, instance_config)

    @_retry
    def _get(self, url, contents_key='contents'):
        method = 'GET'
        headers = self._signed_headers(method, url)
        response = requests.get(url, headers=headers)
        raise_scp_error(response)
        if contents_key is not None:
            return response.json().get(contents_key, [])
        else:
            return response.json()

    @_retry
    def _post(self, url, request_body):
        method = 'POST'
        headers = self._signed_headers(method, url)
        response = requests.post(url, json=request_body, headers=headers)
        raise_scp_error(response)
        return response.json()

    @_retry
    def _delete(self, url, request_body=None):
        method = 'DELETE'
        headers = self._signed_headers(method, url)
        if request_body:
            response = requests.delete(url, json=request_body, headers=headers)
        else:
            response = requests.delete(url, headers=headers)
        raise_scp_error(response)
        return response.json()

    def create_security_group(self, zone_id, vpc_id, sg_name):
        url = f'{API_ENDPOINT}/security-group/v3/security-groups'
        request_body = {
            'loggable': False,
            'securityGroupName': sg_name,
            'serviceZoneId': zone_id,
            'vpcId': vpc_id,
            'securityGroupDescription': 'sky security group'
        }
        return self._post(url, request_body)

    def _security_group_rule_not_exist(self, sg_id, direction, ports):
        response = self.get_security_group_rules(sg_id)
        rules = []
        for rule in response:
            rule_direction = rule['ruleDirection']
            if rule_direction == direction:
                rules.append(rule)
        for rule in rules:
            port_list = ','.join(rule['tcpServices'])
            port = ','.join(ports)
            if port == port_list:
                return False
        return True

    def add_security_group_rule(self, sg_id, direction,
                                ports: Optional[List[str]],
                                cnt: Optional[int]) -> None:
        if ports is None:
            if direction == 'IN':
                if cnt == 1:
                    ports = ['22']
                else:
                    ports = ['22', '6380', '8076', '10001', '11001-11200']
            else:
                if cnt == 1:
                    ports = ['21', '22', '80', '443']
                else:
                    ports = [
                        '21', '22', '80', '443', '6380', '8076', '10001',
                        '11001-11200'
                    ]
        services = []
        for port in ports:
            services.append({'serviceType': 'TCP', 'serviceValue': port})
        if self._security_group_rule_not_exist(sg_id, direction, ports):
            url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}/rules'  # pylint: disable=line-too-long
            if direction == 'IN':
                target_address = 'sourceIpAddresses'
            else:
                target_address = 'destinationIpAddresses'
            request_body = {
                'ruleDirection': direction,
                'services': services,
                target_address: ['0.0.0.0/0'],
                'ruleDescription': 'sky security group rule'
            }
            self._post(url, request_body)
        else:
            return None

    def _firewall_rule_not_exist(self, firewall_id, internal_ip, direction,
                                 ports):
        response = self.get_firewall_rules(firewall_id)
        rules = []
        for rule in response:
            if direction == 'IN':
                if internal_ip == rule['destinationIpAddresses'][0]:
                    rules.append(rule)
            else:
                if internal_ip == rule['sourceIpAddresses'][0]:
                    rules.append(rule)
        for rule in rules:
            port_list = ','.join(rule['tcpServices'])
            port = ','.join(ports)
            if port == port_list:
                return False
        return True

    def add_firewall_rule(self, firewall_id, internal_ip, direction,
                          ports: Optional[List[str]], cnt: Optional[int]):
        if ports is None:
            if direction == 'IN':
                if cnt == 1:
                    ports = ['22']
                else:
                    ports = ['22', '6380', '8076', '10001', '11001-11200']
            else:
                if cnt == 1:
                    ports = ['21', '22', '80', '443']
                else:
                    ports = [
                        '21', '22', '80', '443', '6380', '8076', '10001',
                        '11001-11200'
                    ]
        services = []
        for port in ports:
            services.append({'serviceType': 'TCP', 'serviceValue': port})
        if self._firewall_rule_not_exist(firewall_id, internal_ip, direction,
                                         ports):
            url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
            if direction == 'IN':
                source_ip = '0.0.0.0/0'
                destination_ip = internal_ip
            else:
                source_ip = internal_ip
                destination_ip = '0.0.0.0/0'
            request_body = {
                'sourceIpAddresses': [source_ip],
                'destinationIpAddresses': [destination_ip],
                'services': services,
                'ruleDirection': direction,
                'ruleAction': 'ALLOW',
                'isRuleEnabled': True,
                'ruleLocationType': 'FIRST',
                'ruleDescription': 'sky firewall rule'
            }
            return self._post(url, request_body)
        else:
            return None

    def terminate_instance(self, instance_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{instance_id}'
        return self._delete(url)

    def get_instances(self) -> List[dict]:
        """List existing instances."""
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers'
        return self._get(url)

    def get_catalog(self) -> Dict[str, Any]:
        """List offered instances and their availability."""
        url = f'{API_ENDPOINT}/instance-types'
        headers = self._signed_headers('GET', url)
        response = requests.get(url, headers=headers)
        raise_scp_error(response)
        return response.json().get('data', [])

    def get_signature(self,
                      method: str,
                      url: str,
                      timestamp: Optional[str] = None) -> str:
        if timestamp is None:
            timestamp = str(int(time.time() * 1000))

        url_info = parse.urlsplit(url)
        url = (f'{url_info.scheme}://{url_info.netloc}'
               f'{parse.quote(url_info.path)}')
        if url_info.query:
            enc_params = list(
                map(lambda item: (item[0], parse.quote(item[1][0])),
                    parse.parse_qs(url_info.query).items()))
            url = f'{url}?{parse.urlencode(enc_params)}'

        message = method + url + timestamp \
                  + self.access_key + self.project_id + self.client_type
        message = bytes(message, 'utf-8')
        secret = bytes(self.secret_key, 'utf-8')
        signature = str(
            base64.b64encode(
                hmac.new(secret, message, digestmod=hashlib.sha256).digest()),
            'utf-8')

        return str(signature)

    def get_nic(self, instance_id) -> List[dict]:
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{instance_id}/nics'  # pylint: disable=line-too-long
        return self._get(url)

    def get_external_ip(self, instance_id, ip):
        nics = self.get_nic(instance_id=instance_id)
        for nic in nics:
            if (nic['ip'] == ip and nic['subnetType'] == 'PUBLIC'):
                return nic['natIp']
        return None

    def get_zones(self) -> List[dict]:
        url = f'{API_ENDPOINT}/project/v3/projects/{self.project_id}/zones'
        return self._get(url)

    def get_vpcs(self, service_zone_id) -> List[dict]:
        url = f'{API_ENDPOINT}/vpc/v2/vpcs?serviceZoneId={service_zone_id}'
        return self._get(url)

    def get_subnets(self) -> List[dict]:
        url = f'{API_ENDPOINT}/subnet/v2/subnets?subnetTypes=PUBLIC'
        return self._get(url)

    def delete_security_group(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}'
        return self._delete(url)

    def delete_firewall_rule(self, firewall_id, rule_ids):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        request_body = {'ruleDeletionType': 'PARTIAL', 'ruleIds': rule_ids}
        return self._delete(url, request_body=request_body)

    def get_security_groups(self, vpc_id=None, sg_name=None):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups'
        parameter = []
        if vpc_id is not None:
            parameter.append('vpcId=' + vpc_id)
        if sg_name is not None:
            parameter.append('securityGroupName=' + sg_name)
        if len(parameter) > 0:
            url = url + '?' + '&'.join(parameter)
        return self._get(url)

    def get_internet_gateway(self):
        url = f'{API_ENDPOINT}/internet-gateway/v2/internet-gateways'
        return self._get(url)

    def get_firewall_rule_info(self, firewall_id, rule_id):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules/{rule_id}'  # pylint: disable=line-too-long
        return self._get(url, contents_key=None)

    def get_firewalls(self):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls'
        return self._get(url)

    def get_service_zone_names(self):
        url = f'{API_ENDPOINT}/project/v3/projects/{self.project_id}/zones'
        zone_contents = self._get(url)
        return [content['serviceZoneName'] for content in zone_contents]

    def start_instance(self, instance_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{instance_id}/start'  # pylint: disable=line-too-long
        return self._post(url=url, request_body={})

    def stop_instance(self, instance_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{instance_id}/stop'  # pylint: disable=line-too-long
        return self._post(url=url, request_body={})

    def get_security_group_rules(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}/rules'
        return self._get(url)

    def get_firewall_rules(self, firewall_id):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        return self._get(url)

    def get_instance_info(self, instance_id):
        url = f'{API_ENDPOINT}/virtual-server/v3/virtual-servers/{instance_id}'
        return self._get(url=url, contents_key=None)

    def create_vpc(self, zone_id):
        vpc_name = 'skyvpc' + zone_id[5:10]
        request_body = {
            'serviceZoneId': zone_id,
            'vpcName': vpc_name,
            'vpcDescription': 'sky vpc'
        }
        url = f'{API_ENDPOINT}/vpc/v3/vpcs'
        return self._post(url, request_body)

    def create_subnet(self, vpc_id, zone_id):
        subnet_name = 'skysubnet' + zone_id[5:10]
        request_body = {
            'subnetCidrBlock': '192.168.0.0/24',
            'subnetName': subnet_name,
            'subnetType': 'PUBLIC',
            'vpcId': vpc_id,
            'subnetDescription': 'sky subnet'
        }
        url = f'{API_ENDPOINT}/subnet/v2/subnets'
        return self._post(url, request_body)

    def create_internet_gateway(self, vpc_id):
        request_body = {
            'firewallEnabled': True,
            'firewallLoggable': False,
            'internetGatewayType': 'SHARED',
            'vpcId': vpc_id,
            'internetGatewayDescription': 'sky internet gateway'
        }
        url = f'{API_ENDPOINT}/internet-gateway/v4/internet-gateways'
        return self._post(url, request_body)

    def get_vpc_info(self, vpc_id):
        url = f'{API_ENDPOINT}/vpc/v2/vpcs/{vpc_id}'
        return self._get(url=url, contents_key=None)

    def get_subnet_info(self, subnet_id):
        url = f'{API_ENDPOINT}/subnet/v2/subnets/{subnet_id}'
        return self._get(url=url, contents_key=None)

    def get_internet_gateway_info(self, internet_gateway_id):
        url = f'{API_ENDPOINT}/internet-gateway/v2/internet-gateways/{internet_gateway_id}'  # pylint: disable=line-too-long
        return self._get(url=url, contents_key=None)

    def get_key_pairs(self):
        url = f'{API_ENDPOINT}/key-pair/v1/key-pairs'
        return self._get(url=url, contents_key=None)

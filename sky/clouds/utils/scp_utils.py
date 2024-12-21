"""SCP Open-API Functions.

This module contains a set of rest api functions accessing SCP Open-API.
"""
import base64
import datetime
from functools import wraps
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib import parse

import requests

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


def raise_scp_error(response: requests.Response) -> None:
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
        self.timestamp = ''
        self.signature = ''

        self.headers = {
            'X-Cmp-AccessKey': f'{self.access_key}',
            'X-Cmp-ClientType': f'{self.client_type}',
            'X-Cmp-ProjectId': f'{self.project_id}',
            'X-Cmp-Timestamp': f'{self.timestamp}',
            'X-Cmp-Signature': f'{self.signature}'
        }

    def create_instance(self, instance_config):
        """Launch new instances."""
        url = f'{API_ENDPOINT}/virtual-server/v3/virtual-servers'
        return self._post(url, instance_config)

    @_retry
    def _get(self, url, contents_key='contents'):
        method = 'GET'
        self.set_timestamp()
        self.set_signature(url=url, method=method)

        response = requests.get(url, headers=self.headers)
        raise_scp_error(response)
        if contents_key is not None:
            return response.json().get(contents_key, [])
        else:
            return response.json()

    @_retry
    def _post(self, url, request_body):
        method = 'POST'
        self.set_timestamp()
        self.set_signature(url=url, method=method)

        response = requests.post(url, json=request_body, headers=self.headers)

        raise_scp_error(response)
        return response.json()

    @_retry
    def _delete(self, url, request_body=None):
        method = 'DELETE'
        self.set_timestamp()
        self.set_signature(url=url, method=method)
        if request_body:
            response = requests.delete(url,
                                       json=request_body,
                                       headers=self.headers)

        else:
            response = requests.delete(url, headers=self.headers)
        raise_scp_error(response)
        return response.json()

    def create_security_group(self, zone_id, vpc, sg_name):
        url = f'{API_ENDPOINT}/security-group/v3/security-groups'
        request_body = {
            'loggable': False,
            'securityGroupName': sg_name,
            'serviceZoneId': zone_id,
            'vpcId': vpc,
            'securityGroupDescription': 'skypilot sg'
        }
        return self._post(url, request_body)

    def add_security_group_in_rule(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}/rules'
        request_body = {
            'ruleDirection': 'IN',
            'services': [{
                'serviceType': 'TCP',
                'serviceValue': '22'
            }],
            'sourceIpAddresses': ['0.0.0.0/0'],
            'ruleDescription': 'skypilot ssh rule'
        }
        return self._post(url, request_body)

    def add_security_group_out_rule(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}/rules'
        request_body = {
            'ruleDirection': 'OUT',
            'services': [{
                'serviceType': 'TCP',
                'serviceValue': '21'
            }, {
                'serviceType': 'TCP',
                'serviceValue': '22'
            }, {
                'serviceType': 'TCP',
                'serviceValue': '80'
            }, {
                'serviceType': 'TCP',
                'serviceValue': '443'
            }],
            'destinationIpAddresses': ['0.0.0.0/0'],
            'ruleDescription': 'skypilot out rule'
        }
        return self._post(url, request_body)

    def add_firewall_inbound_rule(self, firewall_id, internal_ip):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        request_body = {
            'sourceIpAddresses': ['0.0.0.0/0'],
            'destinationIpAddresses': [internal_ip],
            'services': [{
                'serviceType': 'TCP',
                'serviceValue': '22'
            }],
            'ruleDirection': 'IN',
            'ruleAction': 'ALLOW',
            'isRuleEnabled': True,
            'ruleLocationType': 'FIRST',
            'ruleDescription': 'description'
        }
        return self._post(url, request_body)

    def add_firewall_outbound_rule(self, firewall_id, internal_ip):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        request_body = {
            'sourceIpAddresses': [internal_ip],
            'destinationIpAddresses': ['0.0.0.0/0'],
            'services': [{
                'serviceType': 'TCP',
                'serviceValue': '21'
            }, {
                'serviceType': 'TCP',
                'serviceValue': '22'
            }, {
                'serviceType': 'TCP',
                'serviceValue': '80'
            }, {
                'serviceType': 'TCP',
                'serviceValue': '443'
            }],
            'ruleDirection': 'OUT',
            'ruleAction': 'ALLOW',
            'isRuleEnabled': True,
            'ruleLocationType': 'FIRST',
            'ruleDescription': 'description'
        }
        return self._post(url, request_body)

    def terminate_instance(self, vm_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{vm_id}'
        return self._delete(url)

    def list_instances(self) -> List[dict]:
        """List existing instances."""
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers'
        return self._get(url)

    def list_catalog(self) -> Dict[str, Any]:
        """List offered instances and their availability."""
        response = requests.get(f'{API_ENDPOINT}/instance-types',
                                headers=self.headers)
        raise_scp_error(response)
        return response.json().get('data', [])

    def get_signature(self, method: str, url: str) -> str:
        url_info = parse.urlsplit(url)
        url = (f'{url_info.scheme}://{url_info.netloc}'
               f'{parse.quote(url_info.path)}')
        if url_info.query:
            enc_params = list(
                map(lambda item: (item[0], parse.quote(item[1][0])),
                    parse.parse_qs(url_info.query).items()))
            url = f'{url}?{parse.urlencode(enc_params)}'

        message = method + url + self.timestamp \
                  + self.access_key + self.project_id + self.client_type
        message = bytes(message, 'utf-8')
        secret = bytes(self.secret_key, 'utf-8')
        signature = str(
            base64.b64encode(
                hmac.new(secret, message, digestmod=hashlib.sha256).digest()),
            'utf-8')

        return str(signature)

    def set_timestamp(self) -> None:
        self.timestamp = str(
            int(
                round(
                    datetime.datetime.timestamp(datetime.datetime.now() -
                                                datetime.timedelta(minutes=1)) *
                    1000)))
        self.headers['X-Cmp-Timestamp'] = self.timestamp

    def set_signature(self, method: str, url: str) -> None:

        self.signature = self.get_signature(url=url, method=method)
        self.headers['X-Cmp-Signature'] = self.signature

    def list_nic_details(self, virtual_server_id) -> List[dict]:
        """List existing instances."""
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{virtual_server_id}/nics'  # pylint: disable=line-too-long
        return self._get(url)

    def get_external_ip(self, virtual_server_id, ip):
        nic_details_list = self.list_nic_details(
            virtual_server_id=virtual_server_id)
        for nic_details in nic_details_list:
            if (nic_details['ip'] == ip and
                    nic_details['subnetType'] == 'PUBLIC'):
                return nic_details['natIp']
        return None

    def list_zones(self) -> List[dict]:
        url = f'{API_ENDPOINT}/project/v3/projects/{self.project_id}/zones'
        return self._get(url)

    def list_products(self, service_zone_id) -> List[dict]:
        url = f'{API_ENDPOINT}/product/v2/zones/{service_zone_id}/products'
        return self._get(url)

    def list_product_groups(self, service_zone_id) -> List[dict]:
        url = f'{API_ENDPOINT}/product/v2/zones/{service_zone_id}/product-groups'  # pylint: disable=line-too-long
        return self._get(url)

    def list_vpcs(self, service_zone_id) -> List[dict]:
        url = f'{API_ENDPOINT}/vpc/v2/vpcs?serviceZoneId={service_zone_id}'
        return self._get(url)

    def list_subnets(self) -> List[dict]:
        url = f'{API_ENDPOINT}/subnet/v2/subnets?subnetTypes=PUBLIC'
        return self._get(url)

    def del_security_group(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}'
        return self._delete(url)

    def del_firwall_rules(self, firewall_id, rule_id_list):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        request_body = {'ruleDeletionType': 'PARTIAL', 'ruleIds': rule_id_list}
        return self._delete(url, request_body=request_body)

    def list_security_groups(self, vpc_id=None, sg_name=None):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups'
        parameter = []
        if vpc_id is not None:
            parameter.append('vpcId=' + vpc_id)
        if sg_name is not None:
            parameter.append('securityGroupName=' + sg_name)
        if parameter:
            url = url + '?' + '&'.join(parameter)
        return self._get(url)

    def list_igw(self):
        url = f'{API_ENDPOINT}/internet-gateway/v2/internet-gateways'
        return self._get(url)

    def get_vm_info(self, vm_id):
        url = f'{API_ENDPOINT}/virtual-server/v3/virtual-servers/{vm_id}'
        return self._get(url, contents_key=None)

    def get_firewal_rule_info(self, firewall_id, rule_id):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules/{rule_id}'  # pylint: disable=line-too-long
        return self._get(url, contents_key=None)

    def list_firwalls(self):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls'
        return self._get(url)

    def list_service_zone_names(self):
        url = f'{API_ENDPOINT}/project/v3/projects/{self.project_id}/zones'
        zone_contents = self._get(url)
        return [content['serviceZoneName'] for content in zone_contents]

    def start_instance(self, vm_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{vm_id}/start'
        return self._post(url=url, request_body={})

    def stop_instance(self, vm_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{vm_id}/stop'
        return self._post(url=url, request_body={})

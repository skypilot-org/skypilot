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
from urllib import parse

CREDENTIALS_PATH = '~/.scp/scp_credential'
API_ENDPOINT = 'https://openapi.samsungsdscloud.com'
TEMP_VM_JSON_PATH = '/tmp/json/tmp_vm_body.json'



class SCPClientError(Exception):
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
    try:
        resp_json = response.json()
        message = resp_json['message']
    except (KeyError, json.decoder.JSONDecodeError):
        raise SCPClientError(f'Unexpected error. Status code: {status_code}')
    raise SCPClientError(f'{status_code}: {message}')



def singleton(class_):
    instances = {}
    def get_instance(*args, **kwargs):
        if class_ not in instances:
            instances[class_] = class_(*args, **kwargs)
        return instances[class_]
    return get_instance

@singleton
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

    def create_instance(self, instance_config):
        """Launch new instances."""
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers'
        return self._post(url, instance_config)

    def _get(self, url, contents_key='contents'):
        method = 'GET'
        self.set_timestamp()
        self.set_signature(url=url, method=method)

        response = requests.get(url, headers=self.headers)
        raise_scp_error(response)
        if contents_key is not None : return response.json().get(contents_key, [])
        else:  return response.json()

    def _post(self, url, request_body):
        method = 'POST'
        self.set_timestamp()
        self.set_signature(url=url, method=method)

        response = requests.post(url, json=request_body, headers=self.headers)

        raise_scp_error(response)
        return response.json()

    def _delete(self, url, request_body=None):
        method = 'DELETE'
        self.set_timestamp()
        self.set_signature(url=url, method=method)
        if request_body: response = requests.delete(url, json=request_body, headers=self.headers)
        else: response = requests.delete(url,  headers=self.headers)
        raise_scp_error(response)
        return response.json()

    def create_security_group(self, zone_id, product_group, vpc, sg_name):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups'
        request_body = {
            "productGroupId": product_group,
            "securityGroupName": sg_name,
            "serviceZoneId": zone_id,
            "tags": [{
                "tagKey": "tagKey",
                "tagValue": "tagValue"
            }],
            "vpcId": vpc,
            "securityGroupDescription": "skypilot sg"
        }
        return self._post(url, request_body)

    def add_security_group_rule(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}/rules'
        request_body = {
              "ruleDirection" : "IN",
              "services" : [ {
                "serviceType" : "TCP",
                "serviceValue" : "22"
              } ],
              "sourceIpAddresses" : [ "0.0.0.0/0"],
              "ruleDescription" : "skypilot ssh rue"
            }
        return self._post(url, request_body)

    def add_firewall_inbound_rule(self, firewall_id, internal_ip):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        request_body = {
          "sourceIpAddresses" : ["0.0.0.0/0"],
          "destinationIpAddresses" : [internal_ip],
          "services" : [ {
            "serviceType" : "TCP",
            "serviceValue" : "22"
          } ],
          "ruleDirection" : "IN",
          "ruleAction" : "ALLOW",
          "isRuleEnabled" : True,
          "ruleLocationType" : "FIRST",
          "ruleDescription" : "description"
        }
        return self._post(url, request_body)

    def remove_instances(self, *instance_ids: str) -> Dict[str, Any]:
        """Terminate instances."""
        data = json.dumps({
            'instance_ids': [
                instance_ids[0] # TODO(ewzeng) don't hardcode
            ]
        })
        response = requests.post(f'{API_ENDPOINT}/virtual-server/instance-operations/terminate',
                                 data=data,
                                 headers=self.headers)
        raise_scp_error(response)
        return response.json().get('data', []).get('terminated_instances', [])

    def terminate_instance(self, vm_id):
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers/{vm_id}'
        return self._delete(url)


    def list_instances(self) -> List[dict]:
        """List existing instances."""
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers'
        return self._get(url)





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
        url_info = parse.urlsplit(url)
        url = f'{url_info.scheme}://{url_info.netloc}{parse.quote(url_info.path)}'
        if url_info.query:
            enc_params = list(map(lambda item: (item[0], parse.quote(item[1][0])), parse.parse_qs(url_info.query).items()))
            url = f'{url}?{parse.urlencode(enc_params)}'


        print(url)

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




    def list_zones(self) -> List[dict]:
        """List zone ids for the project."""
        url = f'{API_ENDPOINT}/project/v3/projects/{self.project_id}/zones'
        return self._get(url)

    def list_products(self, service_zone_id) -> List[dict]:
        """List zone ids for the project."""
        url = f'{API_ENDPOINT}/product/v2/zones/{service_zone_id}/products'
        return self._get(url)

    def list_product_groups(self, service_zone_id) -> List[dict]:
        """List zone ids for the project."""
        url = f'{API_ENDPOINT}/product/v2/zones/{service_zone_id}/product-groups'
        return self._get(url)

    def list_vpcs(self, service_zone_id) -> List[dict]:
        """List zone ids for the project."""
        url = f'{API_ENDPOINT}/vpc/v2/vpcs?serviceZoneId={service_zone_id}'
        return self._get(url)

    def list_subnets(self) -> List[dict]:
        """List zone ids for the project."""
        url = f'{API_ENDPOINT}/subnet/v2/subnets?subnetTypes=PUBLIC'
        return self._get(url)

    def del_security_group(self, sg_id):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups/{sg_id}'
        return self._delete(url)

    def del_firwall_rule(self, firewall_id, rule_id):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules'
        request_body={
              "ruleDeletionType" : "PARTIAL",
              "ruleIds" : [ rule_id ]
            }
        print(firewall_id)
        print(request_body)

        return self._delete(url, request_body=request_body)



    def list_security_groups(self,  vpc_id=None, sg_name=None):
        url = f'{API_ENDPOINT}/security-group/v2/security-groups'
        parameter =[]
        if vpc_id is not None: parameter.append("vpcId="+vpc_id)
        if sg_name is not None: parameter.append("securityGroupName=" + sg_name)
        if len(parameter) >0 : url = url+"?"+"&".join(parameter)
        return self._get(url)

    def list_igw(self):
        url = f'{API_ENDPOINT}/internet-gateway/v2/internet-gateways'
        return self._get(url)


    def get_vm_info(self, vm_id):
        url = f'{API_ENDPOINT}/virtual-server/v3/virtual-servers/{vm_id}'
        return self._get(url, contents_key=None)

    def get_firewal_rule_info(self, firewall_id, rule_id):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls/{firewall_id}/rules/{rule_id}'
        return self._get(url, contents_key=None)


    def list_firwalls(self):
        url = f'{API_ENDPOINT}/firewall/v2/firewalls'
        return self._get(url)

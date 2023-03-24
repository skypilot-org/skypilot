
zones =[
    {
        "serviceZoneId": "ZONE-FClPklmysrhRpknZ6DaI2f",
        "serviceZoneName": "KOREA-WEST-1-SCP-B001",
    },
    {
        "serviceZoneId": "ZONE-0sf9rZYSstpHsOCn0Z2JGg",
        "serviceZoneName": "KORE_WEST-1-SWN-FIN-B001",
    },
    {
        "serviceZoneId": "ZONE-FddcTr2TtliK3jl0I5G4wj",
        "serviceZoneName": "KOREA-WEST-1-SCP-B002",
    },
    {
        "serviceZoneId": "ZONE-NZoJ-yOrrnhHxcfYsr2IRb",
        "serviceZoneName": "KOREA-EAST-1-CCN-FIN-B001",
    },
    {
        "serviceZoneId": "ZONE-Yi4UK3uHsujPbQYqsRgo7i",
        "serviceZoneName": "KOREA-EAST-1-SCP-B001",
    },
    {
        "serviceZoneId": "ZONE-Mi1p0QoSq3bNPJMb5YVmkc",
        "serviceZoneName": "KOREA-WEST-1-SCP-BO9",
    },
    {
        "serviceZoneId": "ZONE-lxu6F_ntqxeIMaZZwh2I-p",
        "serviceZoneName": "KOREA-WEST-2-SCP-B001",
    }
]

CREDENTIALS_PATH = '~/.scp/scp_credential'
API_ENDPOINT = 'https://openapi.samsungsdscloud.com'

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
import random
class SCPClientSmall:
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

        self.user_name = self._credentials.get('user_name', None)
        self.password = self._credentials.get('password', None)
        self.ssh_private_key_path = self._credentials.get('ssh_private_key_path', None)
        self.ssh_public_key_path = self._credentials.get('ssh_public_key_path', None)

        self.headers = {
            'X-Cmp-AccessKey': f'{self.access_key}',
            'X-Cmp-ClientType': f'{self.client_type}',
            'X-Cmp-ProjectId': f'{self.project_id}',
            'X-Cmp-Timestamp': f'{self.timestamp}',
            'X-Cmp-Signature': f'{self.signature}'
         }
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

    def create_instance(self, instance_config):
        """Launch new instances."""
        url = f'{API_ENDPOINT}/virtual-server/v2/virtual-servers'
        return self._post(url, instance_config)

    def _get(self, url, contents_key='contents'):
        method = 'GET'
        self.set_timestamp()
        self.set_signature(url=url, method=method)

        response = requests.get(url, headers=self.headers)
        return response.json().get(contents_key, [])


    def list_image(self, serviceZoneId, size="999"):
        url = f'{API_ENDPOINT}/image/v2/standard-images'
        parameter = []
        parameter.append("serviceZoneId=" + serviceZoneId)
        parameter.append("size="+size)
        if len(parameter) > 0: url = url + "?" + "&".join(parameter)
        return self._get(url)



if __name__ == '__main__':
    api = SCPClientSmall()
    # for zone in zones:
    #     # print(f'\n\n\n\skypilot:gpu-ubuntu-2004,{zone["serviceZoneName"]}')
    #     zone_id = zone['serviceZoneId']
    #     images = api.list_image(zone_id)
    #     for image in images:
    #         del image['icon']
    #     images = [img for img in images if img["osType"] == "UBUNTU"]
    #     images =[img for img in images if img["osType"] =="UBUNTU" and (img['imageName'] =='Ubuntu 20.04' or img['imageName'] == 'Ubuntu_20.04' )]
    #
    #     for img in images:
    #         print(f'gpu-ubuntu-2004,{zone["serviceZoneName"]},ubuntu,20.04,{img["imageId"]},20211208')


    for zone in zones:
        print(f'\n\n\n{zone["serviceZoneName"]}',zone['serviceZoneId'] )
        zone_id = zone['serviceZoneId']
        images = api.list_image(zone_id)
        for image in images:
            del image['icon']
        images = [img for img in images if img["osType"] == "UBUNTU"]

        for img in images:
            # print(f'gpu-ubuntu-2004,{zone["serviceZoneName"]},ubuntu,20.04,{img["imageId"]},20211208', img['osType'], img['imageName'])
            print( img['osType'],img["imageId"], img['imageName'] )

"""Test program for SkyMap server to periodically probe a region"""

import time

import requests

from sky.map import constants

cloud = 'GCP'
region='us-central1'
accelerator='V100'

while True:
    print('Sending periodic probe', cloud, region, accelerator)
    requests.post(f'http://{constants.SKY_MAP_IP_ADDR}:{constants.SKY_MAP_PORT}/active-explore/{cloud}/{region}/{accelerator}') # pylint: disable=line-too-long
    time.sleep(600)

# http://35.232.140.79:30001/get-all-zone-info
# http://35.232.140.79:30001/visualize-wait/us-central1-a/V100-1

"""Test program for SkyMap server to periodically probe a region"""

import time

import requests

from sky.map import constants

cloud = 'GCP'
region = 'us-central1'
accelerator = 'V100'
password = 'FILL_IN_THE_CORRECT_PASSWORD'

while True:
    print('Sending periodic probe', cloud, region, accelerator)
    requests.post(f'http://{constants.SKY_MAP_IP_ADDR}:{constants.SKY_MAP_PORT}/active-explore/{cloud}/{region}/{accelerator}/{password}')  # pylint: disable=line-too-long
    time.sleep(600)

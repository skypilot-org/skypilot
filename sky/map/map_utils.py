"""Util functions for SkyMap."""

import json
import typing

import requests
from starlette.responses import Response

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

def report_preemption(zone: str, life_time: float):
    """Report preemption to SkyMap."""

    logger.info(
        f'Reporting preemption to SkyMap. zone: {zone}, life_time: {life_time}')
    if zone is None:
        logger.info('No zone specified. Skipping preemption report.')
        return

    json_data = {'zone': zone, 'time': life_time}
    sky_map_ip_addr = constants.SKY_MAP_IP_ADDR
    sky_map_port = constants.SKY_MAP_PORT

    response = requests.post(
        f'http://{sky_map_ip_addr}:{sky_map_port}/add-preempt', json=json_data)

    logger.info(response)


def report_wait(zone: str, wait_time: float):
    """Report preemption to SkyMap."""

    logger.info(
        f'Reporting wait to SkyMap. zone: {zone}, wait_time: {wait_time}')
    if zone is None:
        logger.info('No zone specified. Skipping wait report.')
        return

    json_data = {'zone': zone, 'time': wait_time}
    sky_map_ip_addr = constants.SKY_MAP_IP_ADDR
    sky_map_port = constants.SKY_MAP_PORT

    response = requests.post(
        f'http://{sky_map_ip_addr}:{sky_map_port}/add-wait', json=json_data)

    logger.info(response)

class PrettyJSONResponse(Response):
    media_type = 'application/json'

    def render(self, content: typing.Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=4,
            separators=(', ', ': '),
        ).encode('utf-8')

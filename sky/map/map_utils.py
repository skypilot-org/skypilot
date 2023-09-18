"""Util functions for SkyMap."""

import json
import typing
from typing import Optional

import requests
from starlette.responses import Response

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)


def report_preemption(zone: Optional[str], life_time: float,
                      resource: Optional[str]):
    """Report preemption to SkyMap."""

    logger.info(
        f'Report Preempt. zone:{zone}, life_time:{life_time}, resource:{resource}'  # pylint: disable=line-too-long
    )
    if zone is None:
        logger.info('No zone specified. Skipping preemption report.')
        return

    if resource is None:
        logger.info('No resource specified. Skipping preemption report.')
        return

    json_data = {'zone': zone, 'time': life_time, 'resource': resource}
    sky_map_ip_addr = constants.SKY_MAP_IP_ADDR
    sky_map_port = constants.SKY_MAP_PORT

    response = requests.post(
        f'http://{sky_map_ip_addr}:{sky_map_port}/add-preempt', json=json_data)

    logger.info(response)


def report_wait(zone: Optional[str], wait_time: float, resource: Optional[str]):
    """Report preemption to SkyMap."""

    logger.info(
        f'Report wait. zone:{zone}, wait_time:{wait_time}, resource:{resource}')  # pylint: disable=line-too-long
    if zone is None:
        logger.info('No zone specified. Skipping wait report.')
        return

    if resource is None:
        logger.info('No resource specified. Skipping wait report.')
        return

    json_data = {'zone': zone, 'time': wait_time, 'resource': resource}
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

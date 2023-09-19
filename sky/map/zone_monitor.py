"""SkyMap Zone Monitor: the monitor for different zone

Responsible for monitoring different zones's preempt and wait time data.
"""
import datetime
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.map import constants
from sky.map import zone_store

logger = sky_logging.init_logger('sky.map.zone_monitor')


class ZoneMonitor:
    """ZoneMonitor: monitor different zones' data.

    This class is responsible for:
        - query zone preempt and wait time data
    """

    def __init__(self) -> None:
        self._zone_stores: Dict[str, zone_store.ZoneStore] = {}

    def add_zone_preempt_data(self, zone: str, time_in_s: float,
                              resource: str) -> None:

        if zone not in self._zone_stores:
            self._zone_stores[zone] = zone_store.ZoneStore(zone)
        self._zone_stores[zone].add_preempt_data(time_in_s,
                                                 datetime.datetime.now(),
                                                 resource)

    def add_zone_wait_data(self, zone: str, time_in_s: float,
                           resource: str) -> None:

        if zone not in self._zone_stores:
            self._zone_stores[zone] = zone_store.ZoneStore(zone)
        self._zone_stores[zone].add_wait_data(time_in_s,
                                              datetime.datetime.now(), resource)

    def get_zone_average_wait_time(self, zone: str, time_in_s: float,
                                   resource: str) -> float:
        if zone not in self._zone_stores:
            return constants.UNAVAILABLE_FLOAT
        return self._zone_stores[zone].get_average_wait_time(
            time_in_s, resource)

    def get_zone_average_preempt_time(self, zone: str, time_in_s: float,
                                      resource: str) -> float:

        if zone not in self._zone_stores:
            return constants.UNAVAILABLE_FLOAT
        return self._zone_stores[zone].get_average_preempt_time(
            time_in_s, resource)

    def get_zone_info(self) -> List[Dict[str, str]]:
        zone_info_list = []
        for zone_name, store in self._zone_stores.items():
            preempt_resources, wait_reources = store.get_resources()
            zone_dict = {
                'zone_name': zone_name,
                'preempt_resources': ','.join(preempt_resources),
                'wait_resources': ','.join(wait_reources)
            }
            zone_info_list.append(zone_dict)
        return zone_info_list

    def get_preempt_data_with_idx(
            self, zone: str, idx: int,
            resource: str) -> Tuple[float, Optional[datetime.datetime]]:
        if zone not in self._zone_stores:
            return constants.UNAVAILABLE_FLOAT, None
        return self._zone_stores[zone].get_preempt_data_with_idx(idx, resource)

    def get_wait_data_with_idx(
            self, zone: str, idx: int,
            resource: str) -> Tuple[float, Optional[datetime.datetime]]:
        if zone not in self._zone_stores:
            return constants.UNAVAILABLE_FLOAT, None
        return self._zone_stores[zone].get_wait_data_with_idx(idx, resource)

    def get_num_preempt(self, zone: str, time: float, resource: str):
        if zone not in self._zone_stores:
            return 0
        return self._zone_stores[zone].get_num_preempt(time, resource)

    def get_num_wait(self, zone: str, time: float, resource: str):
        if zone not in self._zone_stores:
            return 0
        return self._zone_stores[zone].get_num_wait(time, resource)

"""SkyMap Zone Monitor: the monitor for different zone

Responsible for monitoring different zones's preempt and wait time data.
"""
import datetime
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.map import zone_store

logger = sky_logging.init_logger('sky.serve.zone_monitor')


class ZoneMonitor:
    """ZoneMonitor: monitor different zones' data.

    This class is responsible for:
        - query zone preempt and wait time data
    """

    def __init__(self) -> None:
        self._zone_stores: Dict[str, zone_store.ZoneStore] = {}

    def add_zone_preempt_data(self, zone: str, time_in_s: float) -> None:

        if zone not in self._zone_stores:
            self._zone_stores[zone] = zone_store.ZoneStore(zone)
        self._zone_stores[zone].add_preempt_data(time_in_s,
                                                 datetime.datetime.now())

    def add_zone_wait_data(self, zone: str, time_in_s: float) -> None:

        if zone not in self._zone_stores:
            self._zone_stores[zone] = zone_store.ZoneStore(zone)
        self._zone_stores[zone].add_wait_data(time_in_s,
                                              datetime.datetime.now())

    def get_zone_average_wait_time(self, zone: str, time_in_s: float) -> float:
        if zone not in self._zone_stores:
            return -1.0
        return self._zone_stores[zone].get_average_wait_time(time_in_s)

    def get_zone_average_preempt_time(self, zone: str,
                                      time_in_s: float) -> float:

        if zone not in self._zone_stores:
            return -1.0
        return self._zone_stores[zone].get_average_preempt_time(time_in_s)

    def get_zone_info(self) -> List[str]:
        return list(self._zone_stores.keys())

    def get_preempt_data_with_idx(
            self, zone: str,
            idx: int) -> Tuple[float, Optional[datetime.datetime]]:
        if zone not in self._zone_stores:
            return -1.0, None
        return self._zone_stores[zone].get_preempt_data_with_idx(idx)

    def get_wait_data_with_idx(
            self, zone: str,
            idx: int) -> Tuple[float, Optional[datetime.datetime]]:
        if zone not in self._zone_stores:
            return -1.0, None
        return self._zone_stores[zone].get_wait_data_with_idx(idx)

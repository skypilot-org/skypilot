"""SkyMap Zone Store: the zone store of the SkyMap server

Responsible for storing different zones's preempt and wait time data.
"""
from datetime import datetime
from typing import List, Optional, Tuple

from sky.map import constants


class ZoneStore:
    """ZoneStore: control everything about zone data.

    This class is responsible for:
        - Storing zone preempt and wait time data
        - Provide a user interface to query these data
    """

    def __init__(self, name: str) -> None:

        self._name = name
        self._preempt_data: List[Tuple[float, datetime]] = []
        self._wait_data: List[Tuple[float, datetime]] = []

    def add_preempt_data(self, time_in_s: float, timestamp: datetime) -> None:
        self._preempt_data.append((time_in_s, timestamp))

    def add_wait_data(self, time_in_s: float, timestamp: datetime) -> None:
        self._wait_data.append((time_in_s, timestamp))

    def get_average_wait_time(self, time_in_s: float) -> float:

        if len(self._wait_data) == 0:
            return 0.0

        total_wait_time = 0.0
        count = 0
        for wait_time, timestamp in self._wait_data:
            time_diff = (datetime.now() - timestamp).total_seconds()
            if time_in_s == -1 or time_diff < time_in_s:
                total_wait_time += wait_time
                count += 1

        if count == 0:
            return 0.0

        return total_wait_time / count

    def get_average_preempt_time(self, time_in_s: float) -> float:

        if len(self._preempt_data) == 0:
            return 0.0

        total_preempt_time = 0.0
        count = 0
        for preempt_time, timestamp in self._preempt_data:
            time_diff = (datetime.now() - timestamp).total_seconds()
            if time_in_s == -1 or time_diff < time_in_s:
                total_preempt_time += preempt_time
                count += 1

        if count == 0:
            return 0.0

        return total_preempt_time / count

    def get_preempt_data_with_idx(self,
                                  idx: int) -> Tuple[float, Optional[datetime]]:
        if idx >= len(self._preempt_data):
            return (constants.UNAVAILABLE_FLOAT, None)
        return (self._preempt_data[idx][0], self._preempt_data[idx][1])

    def get_wait_data_with_idx(self,
                               idx: int) -> Tuple[float, Optional[datetime]]:
        if idx >= len(self._wait_data):
            return (constants.UNAVAILABLE_FLOAT, None)
        return (self._wait_data[idx][0], self._wait_data[idx][1])

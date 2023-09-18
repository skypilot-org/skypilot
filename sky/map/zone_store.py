"""SkyMap Zone Store: the zone store of the SkyMap server

Responsible for storing different zones's preempt and wait time data.
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sky.map import constants


class ZoneStore:
    """ZoneStore: control everything about zone data.

    This class is responsible for:
        - Storing zone preempt and wait time data
        - Provide a user interface to query these data
    """

    def __init__(self, name: str) -> None:

        self._name = name
        self._preempt_data: Dict[str, List[Tuple[float, datetime]]] = {}
        self._wait_data: Dict[str, List[Tuple[float, datetime]]] = {}

    def add_preempt_data(self, time_in_s: float, timestamp: datetime,
                         resource: str) -> None:
        if resource not in self._preempt_data:
            self._preempt_data[resource] = []
        self._preempt_data[resource].append((time_in_s, timestamp))

    def add_wait_data(self, time_in_s: float, timestamp: datetime,
                      resource: str) -> None:
        if resource not in self._preempt_data:
            self._wait_data[resource] = []
        self._wait_data[resource].append((time_in_s, timestamp))

    def get_average_wait_time(self, time_in_s: float, resource: str) -> float:

        if resource not in self._wait_data or len(
                self._wait_data[resource]) == 0:
            return 0.0

        total_wait_time = 0.0
        count = 0
        for wait_time, timestamp in self._wait_data[resource]:
            time_diff = (datetime.now() - timestamp).total_seconds()
            if time_in_s == -1 or time_diff < time_in_s:
                total_wait_time += wait_time
                count += 1

        if count == 0:
            return 0.0

        return total_wait_time / count

    def get_average_preempt_time(self, time_in_s: float,
                                 resource: str) -> float:

        if resource not in self._preempt_data or len(
                self._preempt_data[resource]) == 0:
            return 0.0

        total_preempt_time = 0.0
        count = 0
        for preempt_time, timestamp in self._preempt_data[resource]:
            time_diff = (datetime.now() - timestamp).total_seconds()
            if time_in_s == -1 or time_diff < time_in_s:
                total_preempt_time += preempt_time
                count += 1

        if count == 0:
            return 0.0

        return total_preempt_time / count

    def get_preempt_data_with_idx(
            self, idx: int, resource: str) -> Tuple[float, Optional[datetime]]:
        if resource not in self._preempt_data or idx >= len(
                self._preempt_data[resource]):
            return (constants.UNAVAILABLE_FLOAT, None)
        return (self._preempt_data[resource][idx][0],
                self._preempt_data[resource][idx][1])

    def get_wait_data_with_idx(
            self, idx: int, resource: str) -> Tuple[float, Optional[datetime]]:
        if resource not in self._wait_data or idx >= len(
                self._wait_data[resource]):
            return (constants.UNAVAILABLE_FLOAT, None)
        return (self._wait_data[resource][idx][0],
                self._wait_data[resource][idx][1])

    def get_resources(self) -> Tuple[List[str], List[str]]:
        return list(self._preempt_data.keys()), list(self._wait_data.keys())

    def get_num_preempt(self, time: float, resource: str):
        if resource not in self._preempt_data:
            return 0

        count = 0
        for _, timestamp in self._preempt_data[resource]:
            time_diff = (datetime.now() - timestamp).total_seconds()
            if time == -1 or time_diff < time:
                count += 1

        return count

    def get_num_wait(self, time: float, resource: str):
        if resource not in self._wait_data:
            return 0

        count = 0
        for _, timestamp in self._wait_data[resource]:
            time_diff = (datetime.now() - timestamp).total_seconds()
            if time == -1 or time_diff < time:
                count += 1

        return count

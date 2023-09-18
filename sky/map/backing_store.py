"""SkyMap Backing Store: persistently stores historical preemption information.
"""

import os.path
import pickle

from sky import sky_logging
from sky.map import zone_monitor

logger = sky_logging.init_logger('sky.serve.backing_store')


class BackingStore:
    """BackingStore: back up different zones' data.
    """

    def __init__(self, store_path: str) -> None:
        self._store_path = store_path

    def store(self, monitor: zone_monitor.ZoneMonitor):

        if not os.path.isfile(self._store_path):
            logger.info(
                f'New Backing store address created: {self._store_path}')

        with open(self._store_path, 'wb+') as f:
            pickle.dump(monitor, f)

    def load(self) -> zone_monitor.ZoneMonitor:
        if not os.path.isfile(self._store_path):
            return zone_monitor.ZoneMonitor()

        with open(self._store_path, 'rb') as f:
            monitor = pickle.load(f)

        return monitor

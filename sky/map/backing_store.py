"""SkyMap Backing Store: persistently stores historical preemption information.
"""

import os.path
import pickle

from filelock import FileLock

from sky import sky_logging
from sky.map import zone_monitor

logger = sky_logging.init_logger('sky.map.backing_store')


class BackingStore:
    """BackingStore: back up different zones' data.
    """

    def __init__(self, store_path: str) -> None:
        self._store_path = store_path

    def store(self, monitor: zone_monitor.ZoneMonitor):
        if not os.path.isfile(self._store_path):
            logger.info(
                f'New Backing store address created: {self._store_path}')

        with FileLock(f'{self._store_path}.lck'):
            with open(self._store_path, 'wb+') as f:
                logger.info('Storing Backing Store...')
                pickle.dump(monitor, f)

    def load(self) -> zone_monitor.ZoneMonitor:
        if not os.path.isfile(self._store_path):
            logger.info('No backing store found: default created')
            return zone_monitor.ZoneMonitor()

        with FileLock(f'{self._store_path}.lck'):
            with open(self._store_path, 'rb') as f:
                logger.info('Loading Backing Store...')
                monitor = pickle.load(f)

        return monitor

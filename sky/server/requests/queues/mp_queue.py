"""Shared queues for multiprocessing."""
import queue
import time
from typing import List

from queue_manager import manager
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# The default port used by SkyPilot API server's request queue.
# We avoid 50010, as it might be taken by HDFS.
DEFAULT_QUEUE_MANAGER_PORT = 50011


def start_queue_manager(queue_names: List[str],
                        port: int = DEFAULT_QUEUE_MANAGER_PORT) -> None:
    manager.start(queue_names, port)


def get_queue(queue_name: str,
              port: int = DEFAULT_QUEUE_MANAGER_PORT) -> queue.Queue:
    return manager.get_queue(queue_name, port)


def wait_for_queues_to_be_ready(queue_names: List[str],
                                port: int = DEFAULT_QUEUE_MANAGER_PORT) -> None:
    """Wait for the queues to be ready after queue manager is just started."""
    initial_time = time.time()
    max_wait_time = 5
    while queue_names:
        try:
            get_queue(queue_names[0], port)
            queue_names.pop(0)
            break
        except ConnectionRefusedError as e:  # pylint: disable=broad-except
            logger.info(f'Waiting for request queue, named {queue_names[0]!r}, '
                        f'to be ready...')
            time.sleep(0.2)
            if time.time() - initial_time > max_wait_time:
                raise RuntimeError(
                    f'Request queue, named {queue_names[0]!r}, '
                    f'is not ready after {max_wait_time} seconds.') from e

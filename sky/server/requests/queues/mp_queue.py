"""Shared queues for multiprocessing."""
from multiprocessing import managers
import queue
import time
from typing import List

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

DEFAULT_QUEUE_MANAGER_PORT = 50010


# Have to create custom manager to handle different processes connecting to the
# same manager and getting the same queues.
class QueueManager(managers.BaseManager):
    pass


def start_queue_manager(queue_names: List[str],
                        port: int = DEFAULT_QUEUE_MANAGER_PORT) -> None:
    # Defining a local function instead of a lambda function
    # (e.g. lambda: q) because the lambda function captures q by
    # reference, so by the time lambda is called, the loop has already
    # reached the last q item, causing the manager to always return
    # the last q item.
    def queue_getter(q_obj):
        return lambda: q_obj

    for name in queue_names:
        q_obj: queue.Queue = queue.Queue()
        QueueManager.register(name, callable=queue_getter(q_obj))

    # Start long-running manager server.
    manager = QueueManager(address=('', port), authkey=b'skypilot')
    server = manager.get_server()
    server.serve_forever()


def get_queue(queue_name: str,
              port: int = DEFAULT_QUEUE_MANAGER_PORT) -> queue.Queue:
    QueueManager.register(queue_name)
    manager = QueueManager(address=('localhost', port), authkey=b'skypilot')
    manager.connect()
    return getattr(manager, queue_name)()


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

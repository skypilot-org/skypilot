"""Shared queues for multiprocessing."""
import multiprocessing
from multiprocessing import managers
import queue
import time
from typing import List

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# The default port used by SkyPilot API server's request queue.
# We avoid 50010, as it might be taken by HDFS.
DEFAULT_QUEUE_MANAGER_PORT = 50011


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
    # Manager will set socket.SO_REUSEADDR, but BSD and Linux have different
    # behaviors on this option:
    # - BSD(e.g. MacOS): * (0.0.0.0) does not conflict with other addresses on
    #   the same port
    # - Linux: in the contrary, * conflicts with any other addresses
    # So on BSD systems, binding to * while the port is already bound to
    # localhost (127.0.0.1) will succeed, but the Manager won't actually be able
    # to accept connections on localhost.
    # For portability, we use the loopback address instead of *.
    manager = QueueManager(address=('localhost', port), authkey=b'skypilot')
    server = manager.get_server()
    server.serve_forever()


def get_queue(queue_name: str,
              port: int = DEFAULT_QUEUE_MANAGER_PORT) -> queue.Queue:
    QueueManager.register(queue_name)
    manager = QueueManager(address=('localhost', port), authkey=b'skypilot')
    manager.connect()
    return getattr(manager, queue_name)()


def wait_for_queues_to_be_ready(queue_names: List[str],
                                queue_server: multiprocessing.Process,
                                port: int = DEFAULT_QUEUE_MANAGER_PORT) -> None:
    """Wait for the queues to be ready after queue manager is just started."""
    initial_time = time.time()
    # Wait for queue manager to be ready. Exit eagerly if the manager process
    # exits, just wait for a long timeout that is unlikely to reach otherwise.
    max_wait_time = 60
    while queue_names:
        try:
            get_queue(queue_names[0], port)
            queue_names.pop(0)
            break
        except ConnectionRefusedError as e:  # pylint: disable=broad-except
            logger.info(f'Waiting for request queue, named {queue_names[0]!r}, '
                        f'to be ready...')
            time.sleep(0.2)
            if not queue_server.is_alive():
                raise RuntimeError(
                    'Queue manager process exited unexpectedly.') from e
            if time.time() - initial_time > max_wait_time:
                raise RuntimeError(
                    f'Request queue, named {queue_names[0]!r}, '
                    f'is not ready after {max_wait_time} seconds.') from e

"""Shared queues for multiprocessing."""
from multiprocessing import managers
import queue
from typing import List

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

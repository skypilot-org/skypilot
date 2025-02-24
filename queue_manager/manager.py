"""Manager manages shared queues for multiprocessing."""
from multiprocessing import managers
import queue
from typing import List


# Have to create custom manager to handle different processes connecting to the
# same manager and getting the same queues.
class QueueManager(managers.BaseManager):
    pass


def start(queue_names: List[str], port: int) -> None:
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


def get_queue(queue_name: str, port: int) -> queue.Queue:
    QueueManager.register(queue_name)
    manager = QueueManager(address=('localhost', port), authkey=b'skypilot')
    manager.connect()
    return getattr(manager, queue_name)()

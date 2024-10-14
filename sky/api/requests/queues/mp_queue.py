"""Shared queues for multiprocessing."""
import multiprocessing
from multiprocessing import managers
import queue
from typing import Dict, List


# Have to create custom manager to handle different processes connecting to the
# same manager and getting the same queues.
class QueueManager(managers.BaseManager):
    pass


def _register_queue(queue_names: List[str]):
    queues: Dict[str,
                 queue.Queue] = {name: queue.Queue() for name in queue_names}
    for name, q in queues.items():
        QueueManager.register(name, callable=lambda: q)


def create_mp_queues(queue_names: List[str], port: int = 50010):
    _register_queue(queue_names)

    # Try to start the manager if it is not running.
    def _start_manager():
        try:
            manager = QueueManager(address=('', port), authkey=b'skypilot')
            server = manager.get_server()
            server.serve_forever()
        except ConnectionError:
            pass

    server_process = multiprocessing.Process(target=_start_manager)
    server_process.start()


def get_queue(queue_name: str, port: int = 50010) -> queue.Queue:
    QueueManager.register(queue_name)
    manager = QueueManager(address=('localhost', port), authkey=b'skypilot')
    manager.connect()
    return getattr(manager, queue_name)()


if __name__ == '__main__':
    # Use a different port to avoid conflicts with existing SkyPilot API server.
    create_mp_queues(['test1', 'test2'], port=50021)

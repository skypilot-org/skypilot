"""Process-local queue implementation."""
import queue
import threading
from typing import Dict

# Global dict to store queues
_queues: Dict[str, queue.Queue] = {}
_lock = threading.Lock()


def get_queue(queue_name: str) -> queue.Queue:
    """Get or create a queue by name."""
    with _lock:
        if queue_name not in _queues:
            _queues[queue_name] = queue.Queue()
        return _queues[queue_name]

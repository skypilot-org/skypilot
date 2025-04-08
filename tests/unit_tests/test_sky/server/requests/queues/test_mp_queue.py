import multiprocessing
import time
from typing import List

from sky.server.requests.queues import mp_queue


def _produce(q_name: str, items: List[str], port: int):
    q = mp_queue.get_queue(q_name, port)
    for item in items:
        q.put(item)


def _validate_consumption(q_name: str, expected_items: List[str], port: int):
    q = mp_queue.get_queue(q_name, port)
    assert q.qsize() == len(
        expected_items
    ), f'Queue {q_name} has {q.qsize()} items, expected {len(expected_items)}'
    for expected_item in expected_items:
        assert q.get() == expected_item
    assert q.empty()


def test_mp_queue():
    q_names = ['test_queue1', 'test_queue2', 'test_queue3']
    port = 50015
    server = multiprocessing.Process(target=mp_queue.start_queue_manager,
                                     args=(q_names, port))
    server.start()
    time.sleep(1)

    q_to_items = {
        q_names[0]: [],
        q_names[1]: ['item1_from_q2'],
        q_names[2]: ['item1_from_q3', 'item2_from_q3'],
    }

    # Produce items to queues in different processes
    producer_processes = []
    for q_name, items in q_to_items.items():
        p = multiprocessing.Process(target=_produce, args=(q_name, items, port))
        p.start()
        producer_processes.append(p)
    for p in producer_processes:
        p.join()

    # Validate consumption in different processes
    consumer_processes = []
    for q_name, expected_items in q_to_items.items():
        p = multiprocessing.Process(target=_validate_consumption,
                                    args=(q_name, expected_items, port))
        p.start()
        consumer_processes.append(p)
    for p in consumer_processes:
        p.join()

    server.terminate()
    server.join()

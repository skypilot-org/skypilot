import multiprocessing
import time
from typing import List
import uuid

import memory_profiler

from sky.server.requests.queues import mp_queue
from sky.utils import common_utils


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
    port = common_utils.find_free_port(50015)
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


def test_mp_queue_memory_footprint():
    q_names = ['test_queue']
    port = common_utils.find_free_port(50115)
    server = multiprocessing.Process(target=mp_queue.start_queue_manager,
                                     args=(q_names, port))
    server.start()
    mp_queue.wait_for_queues_to_be_ready(q_names.copy(), server, port=port)

    def get_memory_usage():
        return memory_profiler.memory_usage(server.pid, interval=0.1,
                                            timeout=1)[0]

    memory_before = get_memory_usage()
    # Tested 1M items takes 160MB memory, use 10K here to accelerate the test
    count = 10_000
    mem_threshold_mb = 18
    q = mp_queue.get_queue(q_names[0], port)
    for _ in range(count):
        # Mock (request_id, ignore_return_value) tuple
        input_tuple = (uuid.uuid4(), True)
        q.put(input_tuple)
    memory_after_put = get_memory_usage()
    for _ in range(count):
        input_tuple = (uuid.uuid4(), True)
        q.put(input_tuple)
    memory_after_put_again = get_memory_usage()
    for _ in range(count * 2):
        q.get()
    # Wait GC in the server process
    time.sleep(1)
    memory_after = get_memory_usage()
    server.terminate()
    server.join()
    print(f'memory usage: {memory_after_put - memory_before}')
    assert memory_after_put - memory_before < mem_threshold_mb, (
        f'Queuing {count} items increased memory usage by {memory_after_put - memory_before}MB, '
        'which is more than the allowed 18MB')
    # Test if the memory usage is proportional to the number of items
    assert memory_after_put_again - memory_after_put < mem_threshold_mb, (
        f'Queuing {count} items increased memory usage by {memory_after_put_again - memory_after_put}MB, '
        'memory usage should be proportional to the number of items')
    print(
        f'memory usage after processing all the items: {memory_after - memory_before}'
    )
    assert memory_after - memory_before < 5, (
        f'Memory usage increased by {memory_after - memory_before}MB after processing all the items,'
        'potential memory leak')

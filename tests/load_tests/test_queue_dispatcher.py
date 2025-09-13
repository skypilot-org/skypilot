import multiprocessing
import threading
import time
import uuid

from sky import sky_logging
from sky.server import config as server_config
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import process
from sky.server.requests import requests as api_requests
from sky.server.requests.queues import mp_queue
from sky.skylet import constants
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)


def disable_logger() -> None:
    server_logger = sky_logging.init_logger('sky.server')
    server_logger.handlers.clear()
    server_logger.propagate = False


disable_logger()


class MockExecutor(process.BurstableExecutor):

    def submit_until_success(self, *args, **kwargs):
        return None


def dummy_func(*args, **kwargs):
    pass


def schedule():
    worker = executor.RequestWorker(
        schedule_type=api_requests.ScheduleType.LONG,
        config=server_config.WorkerConfig(garanteed_parallelism=1,
                                          burstable_parallelism=1))
    mock_executor = MockExecutor(garanteed_workers=1, burst_workers=1)
    queue = executor._get_queue(api_requests.ScheduleType.LONG)  # pylint: disable=protected-access
    while not queue.queue.empty():
        worker.process_request(mock_executor, queue)


def benchmark_queue_dispatcher(queue_backend: str,
                               worker_mode: str,
                               count=1) -> float:
    api_requests.reset_db_and_logs()
    num_requests = 10000
    executor.queue_backend = queue_backend

    queue_server = None
    if queue_backend == executor.QueueBackend.MULTIPROCESSING:
        port = mp_queue.DEFAULT_QUEUE_MANAGER_PORT
        queue_server = multiprocessing.Process(
            target=mp_queue.start_queue_manager,
            args=([api_requests.ScheduleType.LONG.value], port))
        queue_server.start()
        mp_queue.wait_for_queues_to_be_ready(
            [api_requests.ScheduleType.LONG.value], queue_server, port=port)
    elif queue_backend == executor.QueueBackend.LOCAL:
        pass
    else:
        raise RuntimeError(f'Invalid queue backend: {queue_backend}')

    def enqueue_requests():
        for _ in range(num_requests):
            body = payloads.StatusBody()
            body.env_vars = dict({
                constants.USER_ID_ENV_VAR: common_utils.generate_user_hash(),
            })
            executor.schedule_request(
                request_id=str(uuid.uuid4()),
                request_name='status',
                is_skypilot_system=False,
                request_body=body,
                func=dummy_func,
                schedule_type=api_requests.ScheduleType.LONG)

    enqueue_thread = threading.Thread(target=enqueue_requests)
    enqueue_thread.start()
    enqueue_thread.join()

    start_time = time.time()
    if worker_mode == 'thread':
        threads = []
        for _ in range(count):
            schedule_thread = threading.Thread(target=schedule)
            schedule_thread.start()
            threads.append(schedule_thread)
        for t in threads:
            t.join()
    else:
        procs = []
        for _ in range(count):
            schedule_proc = multiprocessing.Process(target=schedule)
            schedule_proc.start()
            procs.append(schedule_proc)
        for p in procs:
            p.join()
    end_time = time.time()
    duration = end_time - start_time
    requests_per_second = num_requests / duration
    if queue_server:
        queue_server.terminate()
        queue_server.join()
    # Clear queue cache
    executor._get_queue.cache_clear()  # pylint: disable=protected-access
    return requests_per_second


if __name__ == '__main__':
    t1 = benchmark_queue_dispatcher(executor.QueueBackend.MULTIPROCESSING,
                                    'process')
    print('\nBenchmark Results:')
    print('-' * 50)
    print(f'Process queue + 1 process dispatcher:')
    print(f'  Requests/sec:  {t1:,.2f}')

    t2 = benchmark_queue_dispatcher(executor.QueueBackend.LOCAL, 'thread')
    print(f'\nLocal thread queue + 1 thread dispatcher:')
    print(f'  Requests/sec:  {t2:,.2f}')

    t3 = benchmark_queue_dispatcher(executor.QueueBackend.MULTIPROCESSING,
                                    'thread')
    print(f'\nProcess queue + 1 thread dispatcher:')
    print(f'  Requests/sec:  {t3:,.2f}')

    t4 = benchmark_queue_dispatcher(executor.QueueBackend.MULTIPROCESSING,
                                    'process', 10)
    print(f'\nProcess queue + 10 process dispatcher:')
    print(f'  Requests/sec:  {t4:,.2f}')

    t5 = benchmark_queue_dispatcher(executor.QueueBackend.LOCAL, 'thread', 10)
    print(f'\nLocal thread queue + 10 thread dispatcher (in one process):')
    print(f'  Requests/sec:  {t5:,.2f}')

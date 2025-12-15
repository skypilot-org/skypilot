import argparse
import asyncio
import multiprocessing as mp
import random
import time
from typing import Optional

from sky.server.requests import payloads
from sky.server.requests import requests as request_lib
from sky.server.requests.requests import Request
from sky.server.requests.requests import RequestStatus
from sky.skylet import constants as sky_constants
from sky.utils import common_utils


def _dummy_entrypoint():
    """Callable placeholder for request entrypoint."""
    return None


def _build_request(user_id: str) -> Request:
    """Create a minimal request record for concurrency testing."""
    request_body = payloads.RequestBody(
        env_vars={sky_constants.USER_ID_ENV_VAR: user_id},
        entrypoint='concurrency_test',
        entrypoint_command='',
        using_remote_api_server=False,
        override_skypilot_config={},
        override_skypilot_config_path=None,
    )
    return Request(
        request_id=request_lib.get_new_request_id(),
        name='concurrency_test',
        entrypoint=_dummy_entrypoint,
        request_body=request_body,
        status=RequestStatus.PENDING,
        created_at=time.time(),
        user_id=user_id,
    )


def _pick_request_id(shared_ids, own_id: str) -> Optional[str]:
    """Pick a request id, preferring ones written by other processes."""
    snapshot = list(shared_ids)
    if not snapshot:
        return None
    others = [rid for rid in snapshot if rid != own_id]
    if others:
        return random.choice(others)
    return random.choice(snapshot)


async def _worker_loop(proc_idx: int, shared_ids, interval: float,
                       operations: int) -> None:
    user_id = common_utils.get_user_hash()
    counters = {
        'create_attempt': 0,
        'create_success': 0,
        'status_update': 0,
        'get_request': 0,
        'get_status': 0,
    }
    last_log = time.time()
    for _ in range(operations):
        counters['create_attempt'] += 1
        request = _build_request(user_id)
        created = await request_lib.create_if_not_exists_async(request)
        if not created:
            continue

        counters['create_success'] += 1
        shared_ids.append(request.request_id)
        await request_lib.update_status_async(request.request_id,
                                              RequestStatus.RUNNING)
        counters['status_update'] += 1

        target_id = _pick_request_id(shared_ids, request.request_id)
        if target_id is not None:
            await request_lib.get_request_async(target_id)
            await request_lib.get_request_status_async(target_id,
                                                       include_msg=False)
            counters['get_request'] += 1
            counters['get_status'] += 1
            await request_lib.update_status_async(
                target_id,
                random.choice(
                    [RequestStatus.RUNNING, RequestStatus.SUCCEEDED]),
            )
            counters['status_update'] += 1

        now = time.time()
        if now - last_log >= 5:
            print(
                f'[proc {proc_idx}] ops '
                f'create_attempt={counters["create_attempt"]} '
                f'create_success={counters["create_success"]} '
                f'status_update={counters["status_update"]} '
                f'get_request={counters["get_request"]} '
                f'get_status={counters["get_status"]} '
                f'shared_ids={len(shared_ids)}')
            last_log = now

    print(
        f'[proc {proc_idx}] completed '
        f'create_attempt={counters["create_attempt"]} '
        f'create_success={counters["create_success"]} '
        f'status_update={counters["status_update"]} '
        f'get_request={counters["get_request"]} '
        f'get_status={counters["get_status"]} '
        f'shared_ids={len(shared_ids)}')


def _worker_main(proc_idx: int, shared_ids, interval: float,
                 operations: int) -> None:
    asyncio.run(_worker_loop(proc_idx, shared_ids, interval, operations))


def main():
    parser = argparse.ArgumentParser(
        description='Concurrent request DB stress test.')
    parser.add_argument('-n',
                        '--processes',
                        type=int,
                        default=max(2, mp.cpu_count() or 2),
                        help='Number of worker processes.')
    parser.add_argument('--operations',
                        type=int,
                        default=1000000,
                        help='Number of request cycles per process.')
    args = parser.parse_args()

    with mp.Manager() as manager:
        shared_ids = manager.list()
        processes = [
            mp.Process(target=_worker_main,
                       args=(idx, shared_ids, 0,
                             args.operations))
            for idx in range(args.processes)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join()


if __name__ == '__main__':
    main()

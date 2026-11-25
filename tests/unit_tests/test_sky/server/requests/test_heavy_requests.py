"""Unit tests for the heavy request pool."""
import concurrent.futures
import time
from typing import List
from unittest import mock

import pytest

from sky.server import config as server_config
from sky.server.requests import executor
from sky.server.requests import heavy_requests
from sky.server.requests import payloads
from sky.server.requests import request_names
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.utils import locks

_RN = request_names.RequestName
_HEAVY = requests_lib.ScheduleType.HEAVY
_SHORT = requests_lib.ScheduleType.SHORT

# (request name, body, heavy). Every shape in HEAVY_SHAPES needs a heavy case
# and a light case here.
_SHAPES = [
    pytest.param(_RN.CLUSTER_COST_REPORT,
                 payloads.CostReportBody(),
                 True,
                 id='cost_report'),
    pytest.param(_RN.CLUSTER_COST_REPORT,
                 payloads.CostReportBody(days=None),
                 True,
                 id='cost_report-default-days'),
    pytest.param(_RN.CLUSTER_COST_REPORT,
                 payloads.CostReportBody(cluster_names=['c']),
                 False,
                 id='cost_report-cluster-names'),
    pytest.param(_RN.CLUSTER_COST_REPORT,
                 payloads.CostReportBody(cluster_hashes=['h']),
                 False,
                 id='cost_report-cluster-hashes'),
    pytest.param(_RN.CLUSTER_COST_REPORT,
                 payloads.CostReportBody(cluster_names=[]),
                 False,
                 id='cost_report-no-clusters'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(),
                 True,
                 id='queue_v2'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(all_users=True),
                 True,
                 id='queue_v2-all-users'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(limit=101),
                 True,
                 id='queue_v2-limit-101'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(limit=100),
                 False,
                 id='queue_v2-limit-100'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(limit=50, all_users=True),
                 False,
                 id='queue_v2-all-users-limit-50'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(skip_finished=True),
                 False,
                 id='queue_v2-skip-finished'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(job_ids=[1]),
                 False,
                 id='queue_v2-job-ids'),
    pytest.param(_RN.JOBS_QUEUE_V2,
                 payloads.JobsQueueV2Body(job_ids=[]),
                 False,
                 id='queue_v2-no-jobs'),
    pytest.param(_RN.JOBS_QUEUE, payloads.JobsQueueBody(), True, id='queue'),
    pytest.param(_RN.JOBS_QUEUE,
                 payloads.JobsQueueBody(all_users=True),
                 True,
                 id='queue-all-users'),
    pytest.param(_RN.JOBS_QUEUE,
                 payloads.JobsQueueBody(skip_finished=True),
                 False,
                 id='queue-skip-finished'),
    pytest.param(_RN.JOBS_QUEUE,
                 payloads.JobsQueueBody(job_ids=[7]),
                 False,
                 id='queue-job-ids'),
    pytest.param(_RN.CLUSTER_STATUS,
                 payloads.StatusBody(all_users=True),
                 False,
                 id='status'),
]


def test_every_heavy_shape_has_heavy_and_light_cases():
    for name in heavy_requests.HEAVY_SHAPES:
        verdicts = {p.values[2] for p in _SHAPES if p.values[0] == name}
        assert verdicts == {True, False}, name


@pytest.mark.parametrize('name,body,heavy', _SHAPES)
def test_is_heavy(name, body, heavy):
    assert heavy_requests.is_heavy(name, body) is heavy


@pytest.fixture()
def heavy_pool(monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS, '2')


@pytest.fixture()
def scheduled(monkeypatch):
    """Captures the schedule type of every request schedule_request_async
    writes."""
    schedule_types: List[requests_lib.ScheduleType] = []

    async def create(request_id, request_name, request_body, func,
                     request_cluster_name, schedule_type, user_id):
        del request_name, request_body, func, request_cluster_name, user_id
        schedule_types.append(schedule_type)
        return mock.Mock(request_id=request_id)

    monkeypatch.setattr(executor, 'stamp_request_body',
                        lambda *args, **kwargs: 'user-a')
    monkeypatch.setattr(executor, '_create_request_async', create)
    monkeypatch.setattr(executor, 'schedule_prepared_request', mock.AsyncMock())
    monkeypatch.setattr(executor, '_execution_policy', None)
    return schedule_types


async def _schedule(name, body):
    await executor.schedule_request_async(
        request_id='r',
        request_name=name,
        request_body=body.model_copy(deep=True),
        func=lambda: None,
        schedule_type=_SHORT)


@pytest.mark.asyncio
@pytest.mark.parametrize('name,body,heavy', _SHAPES)
async def test_heavy_shapes_run_in_the_heavy_pool(name, body, heavy, heavy_pool,
                                                  scheduled):
    await _schedule(name, body)
    assert scheduled == [_HEAVY if heavy else _SHORT]


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, '', '0'])
async def test_disabled_pool_keeps_heavy_shapes_short(value, monkeypatch,
                                                      scheduled):
    if value is None:
        monkeypatch.delenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS,
                           raising=False)
    else:
        monkeypatch.setenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS, value)
    for case in _SHAPES:
        name, body, _ = case.values
        await _schedule(name, body)
    assert scheduled == [_SHORT] * len(_SHAPES)


@pytest.mark.asyncio
async def test_long_requests_stay_long(heavy_pool, scheduled):
    await executor.schedule_request_async(
        request_id='r',
        request_name=_RN.JOBS_QUEUE_V2,
        request_body=payloads.JobsQueueV2Body(refresh=True),
        func=lambda: None,
        schedule_type=requests_lib.ScheduleType.LONG)
    assert scheduled == [requests_lib.ScheduleType.LONG]


class _ListQueue:

    def __init__(self, items):
        self.items = list(items)

    def get(self):
        return self.items.pop(0) if self.items else None

    def put(self, item):
        self.items.append(item)


class _PendingExecutor:
    """Submits without running; the test completes the futures."""

    def __init__(self):
        self.futures = {}

    def submit_until_success(self, fn, request_id, *args):
        del fn, args
        fut = concurrent.futures.Future()
        self.futures[request_id] = fut
        return fut


@pytest.fixture()
def heavy_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path))
    monkeypatch.setattr(executor.locks, 'get_lock', locks.FileLock)
    monkeypatch.setattr(executor.time, 'sleep', lambda seconds: None)
    users = {'a1': 'user-a', 'a2': 'user-a', 'b1': 'user-b'}

    def get_request(request_id, fields):
        assert set(fields) >= {'status', 'user_id', 'name'}
        request = mock.Mock(status=requests_lib.RequestStatus.PENDING,
                            user_id=users[request_id])
        # Mock(name=...) names the mock; it does not set the attribute.
        request.name = 'sky.cost_report'
        return request

    monkeypatch.setattr(requests_lib, 'get_request', get_request)
    return executor.RequestWorker(schedule_type=_HEAVY,
                                  config=server_config.WorkerConfig(
                                      garanteed_parallelism=0,
                                      burstable_parallelism=2,
                                      num_db_connections_per_worker=0))


def _element(request_id):
    return (request_id, False, False)


def _wait_for_lock_handback(worker):
    deadline = time.monotonic() + 10
    while worker._finished_user_locks.empty():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_heavy_worker_runs_one_request_per_user(heavy_worker):
    queue = _ListQueue([_element('a1'), _element('a2'), _element('b1')])
    pool = _PendingExecutor()

    heavy_worker.process_request(pool, queue)
    heavy_worker.process_request(pool, queue)
    heavy_worker.process_request(pool, queue)
    # a2 waits for a1, which is the same user's, and goes to the back.
    assert list(pool.futures) == ['a1', 'b1']
    assert queue.items == [_element('a2')]

    heavy_worker.process_request(pool, queue)
    assert list(pool.futures) == ['a1', 'b1']
    assert queue.items == [_element('a2')]

    pool.futures['a1'].set_result(None)
    _wait_for_lock_handback(heavy_worker)
    heavy_worker.process_request(pool, queue)
    assert list(pool.futures) == ['a1', 'b1', 'a2']
    assert queue.items == []


def test_heavy_worker_releases_the_lock_when_submission_fails(heavy_worker):

    class _FailingExecutor:

        def submit_until_success(self, *args, **kwargs):
            raise RuntimeError('pool is gone')

    queue = _ListQueue([_element('a1'), _element('a2')])
    heavy_worker.process_request(_FailingExecutor(), queue)
    pool = _PendingExecutor()
    heavy_worker.process_request(pool, queue)
    assert list(pool.futures) == ['a2']


def test_heavy_worker_runs_without_a_lock_that_fails(heavy_worker, monkeypatch):

    class _BrokenLock:
        lock_id = 'broken'

        def acquire(self, blocking=True):
            del blocking
            raise RuntimeError('database is down')

    monkeypatch.setattr(executor.locks, 'get_lock',
                        lambda lock_id: _BrokenLock())
    queue = _ListQueue([_element('a1')])
    pool = _PendingExecutor()
    heavy_worker.process_request(pool, queue)
    assert list(pool.futures) == ['a1']
    assert not queue.items


def test_heavy_worker_releases_the_lock_of_a_failed_request(heavy_worker):
    queue = _ListQueue([_element('a1'), _element('a2')])
    pool = _PendingExecutor()
    heavy_worker.process_request(pool, queue)
    pool.futures['a1'].set_exception(RuntimeError('request failed'))
    _wait_for_lock_handback(heavy_worker)
    heavy_worker.process_request(pool, queue)
    assert list(pool.futures) == ['a1', 'a2']


@pytest.mark.parametrize('value,expected', [(None, 0), ('', 0), ('0', 0),
                                            ('2', 2)])
def test_heavy_worker_count(value, expected, monkeypatch):
    if value is None:
        monkeypatch.delenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS,
                           raising=False)
    else:
        monkeypatch.setenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS, value)
    assert server_config.heavy_worker_count() == expected


@pytest.mark.parametrize('value', ['abc', '-1', '1.5'])
def test_invalid_heavy_worker_count_rejected_at_startup(value, monkeypatch):
    monkeypatch.setenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS, value)
    with pytest.raises(ValueError,
                       match=constants.ENV_VAR_SERVER_HEAVY_WORKERS):
        server_config.validate_explicit_worker_counts()


@mock.patch('sky.utils.common_utils.get_mem_size_gb', return_value=48)
@mock.patch('sky.utils.common_utils.get_cpu_count', return_value=12)
def test_heavy_pool_reserves_memory_from_the_short_pool(cpu_count, mem_size_gb,
                                                        monkeypatch):
    monkeypatch.delenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS, raising=False)
    without = server_config.compute_server_config(deploy=True, quiet=True)
    assert without.heavy_worker_config.burstable_parallelism == 0
    monkeypatch.setenv(constants.ENV_VAR_SERVER_HEAVY_WORKERS, '2')
    with_heavy = server_config.compute_server_config(deploy=True, quiet=True)
    assert with_heavy.heavy_worker_config.garanteed_parallelism == 0
    assert with_heavy.heavy_worker_config.burstable_parallelism == 2
    long_workers = with_heavy.long_worker_config.garanteed_parallelism
    assert (with_heavy.short_worker_config.garanteed_parallelism ==
            server_config._max_short_worker_parallism(48, long_workers, 2))
    assert (with_heavy.short_worker_config.garanteed_parallelism <
            without.short_worker_config.garanteed_parallelism)


def test_heavy_requests_reach_clients_as_short():
    request = requests_lib.Request(request_id='r',
                                   name='sky.cost_report',
                                   entrypoint=print,
                                   request_body=payloads.CostReportBody(),
                                   status=requests_lib.RequestStatus.PENDING,
                                   created_at=0.0,
                                   user_id='user-a',
                                   schedule_type=_HEAVY)
    assert request.encode_for_client().schedule_type == 'short'
    row = dict(zip(requests_lib.REQUEST_COLUMNS, request.to_row()))
    assert row['schedule_type'] == 'heavy'
    assert requests_lib.Request.from_row(
        request.to_row()).schedule_type == _HEAVY


def test_heavy_request_records_its_peak_memory(monkeypatch):
    usage, incr = mock.Mock(), mock.Mock()
    monkeypatch.setattr(executor.metrics_utils,
                        'SKY_APISERVER_REQUEST_MEMORY_USAGE_BYTES', usage)
    monkeypatch.setattr(executor.metrics_utils,
                        'SKY_APISERVER_REQUEST_RSS_INCR_BYTES', incr)
    # Every heavy request is the first request of its process.
    monkeypatch.setattr(executor, '_first_request', True)
    proc = mock.Mock()
    proc.memory_info.return_value = mock.Mock(rss=500)
    executor._record_memory_metrics('sky.cost_report',
                                    proc,
                                    rss_begin=100,
                                    peak_rss=900,
                                    one_request_process=True)
    usage.labels.assert_called_once_with(name='sky.cost_report')
    usage.labels.return_value.observe.assert_called_once_with(800)
    incr.labels.assert_not_called()

"""Unit tests for the daemon submission helpers.

`create_or_refresh_internal_daemon_async` mirrors `create_if_not_exists_async`
for daemon requests: exactly one concurrent caller wins the insert race and
gets True (and is expected to enqueue), every other caller gets False after
refreshing env_vars / name / schedule_type on the existing row.

`delete_orphan_internal_daemons_async` removes daemon-shaped rows whose ids
are no longer in INTERNAL_REQUEST_DAEMONS.
"""
import asyncio
import sqlite3
import unittest.mock as mock

import pytest

from sky.server import daemons
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.queues import base as queue_base
from sky.server.requests.requests import RequestStatus
from sky.server.requests.requests import ScheduleType


def _dummy_event():
    return None


@pytest.fixture()
def isolated_database(tmp_path):
    """Create an isolated database per test."""
    temp_db_path = tmp_path / 'requests.db'
    temp_log_path = tmp_path / 'logs'
    temp_log_path.mkdir()

    with mock.patch('sky.server.constants.API_SERVER_REQUEST_DB_PATH',
                    str(temp_db_path)), \
         mock.patch('sky.server.constants.REQUEST_LOG_PATH_PREFIX',
                    str(temp_log_path)):
        requests._DB = None
        yield tmp_path
        requests._DB = None


def _make_daemon(daemon_id: str) -> daemons.InternalRequestDaemon:
    return daemons.InternalRequestDaemon(
        id=daemon_id,
        name=daemons.request_names.RequestName.REQUEST_DAEMON_STATUS_REFRESH,
        event_fn=_dummy_event,
    )


@pytest.mark.asyncio
async def test_create_or_refresh_inserts_new_row(isolated_database,
                                                 monkeypatch):
    """First caller against an empty DB: row inserted, returns True."""
    monkeypatch.setenv('SKYPILOT_RECON_TAG', 'fresh')
    daemon = _make_daemon('alpha-daemon')
    req = requests.build_internal_daemon_request(daemon)
    assert (await requests.create_or_refresh_internal_daemon_async(req)) is True

    row = await requests.get_request_async('alpha-daemon')
    assert row is not None
    assert row.status == RequestStatus.PENDING
    assert row.pid is None
    assert row.request_body.env_vars.get('SKYPILOT_RECON_TAG') == 'fresh'


@pytest.mark.asyncio
async def test_create_or_refresh_existing_row_returns_false_and_refreshes_env(
        isolated_database, monkeypatch):
    """Second caller against an existing row: returns False, env refreshed."""
    monkeypatch.setenv('SKYPILOT_RECON_TAG', 'old')
    daemon = _make_daemon('alpha-daemon')
    req_first = requests.build_internal_daemon_request(daemon)
    assert (await
            requests.create_or_refresh_internal_daemon_async(req_first)) is True

    # Pretend a deploy happened: env flipped, second caller builds a fresh
    # Request with the new env.
    monkeypatch.setenv('SKYPILOT_RECON_TAG', 'new')
    req_second = requests.build_internal_daemon_request(daemon)
    assert (await requests.create_or_refresh_internal_daemon_async(req_second)
           ) is False

    row = await requests.get_request_async('alpha-daemon')
    assert row.request_body.env_vars.get('SKYPILOT_RECON_TAG') == 'new'


@pytest.mark.asyncio
async def test_delete_orphan_removes_rows_not_in_current_set(isolated_database):
    """A '-daemon' row whose id is not in the current set should be deleted;
    a row whose id IS in the current set should survive."""
    # Seed a "current" daemon row.
    keep_req = requests.build_internal_daemon_request(
        _make_daemon('alpha-daemon'))
    assert await requests.create_if_not_exists_async(keep_req)
    # Seed a "legacy" daemon row directly.
    legacy = requests.Request(
        request_id='removed-daemon',
        name='sky.removed',
        entrypoint=_dummy_event,
        request_body=payloads.RequestBody(),
        status=RequestStatus.PENDING,
        created_at=0.0,
        schedule_type=ScheduleType.SHORT,
        user_id='__system__',
    )
    assert await requests.create_if_not_exists_async(legacy)

    await requests.delete_orphan_internal_daemons_async(
        [_make_daemon('alpha-daemon')])

    assert await requests.get_request_async('removed-daemon') is None
    assert await requests.get_request_async('alpha-daemon') is not None


@pytest.mark.asyncio
async def test_delete_orphan_ignores_non_daemon_request_ids(isolated_database):
    """Rows whose id does not end in '-daemon' must not be touched even if
    they are not in the current set."""
    other = requests.Request(
        request_id='some-client-request-uuid',
        name='sky.launch',
        entrypoint=_dummy_event,
        request_body=payloads.RequestBody(),
        status=RequestStatus.PENDING,
        created_at=0.0,
        schedule_type=ScheduleType.SHORT,
        user_id='__system__',
    )
    assert await requests.create_if_not_exists_async(other)

    await requests.delete_orphan_internal_daemons_async([])

    assert await requests.get_request_async('some-client-request-uuid'
                                           ) is not None


def _queued_ids():
    ids = []
    for schedule_type in ScheduleType:
        queue = queue_base.DbQueueBackend(schedule_type.value)
        while (item := queue.get()) is not None:
            ids.append(item[0])
    return sorted(ids)


@pytest.mark.asyncio
async def test_delete_orphan_also_removes_their_queue_entries(
        isolated_database):
    """The queue entry of a removed daemon goes away with its row."""
    for daemon_id in ['alpha-daemon', 'removed-daemon']:
        req = requests.build_internal_daemon_request(_make_daemon(daemon_id))
        assert await requests.create_if_not_exists_async(req)
        await queue_base.DbQueueBackend(req.schedule_type.value).put_async(
            (daemon_id, False, True))

    await requests.delete_orphan_internal_daemons_async(
        [_make_daemon('alpha-daemon')])

    assert _queued_ids() == ['alpha-daemon']


@pytest.mark.asyncio
async def test_delete_requests_also_removes_their_queue_entries(
        isolated_database):
    """Deleting a request deletes its queue entry and keeps the others."""
    for daemon_id in ['alpha-daemon', 'removed-daemon']:
        req = requests.build_internal_daemon_request(_make_daemon(daemon_id))
        assert await requests.create_if_not_exists_async(req)
        await queue_base.DbQueueBackend(req.schedule_type.value).put_async(
            (daemon_id, False, True))

    await requests.SqliteRequestBackend().delete_requests(['removed-daemon'])

    assert _queued_ids() == ['alpha-daemon']


async def _seed_queued_requests(count, payload_sizes=None):
    """Create `count` daemon requests, each with a queue entry."""
    ids = [f'request-{index:03}-daemon' for index in range(count)]
    for request_id in ids:
        req = requests.build_internal_daemon_request(_make_daemon(request_id))
        assert await requests.create_if_not_exists_async(req)
        await queue_base.DbQueueBackend(req.schedule_type.value).put_async(
            (request_id, False, True))
    if payload_sizes is not None:
        with sqlite3.connect(requests._DB.db_path) as conn:  # pylint: disable=protected-access
            for request_id, size in zip(ids, payload_sizes):
                conn.execute(
                    f'UPDATE {requests.REQUEST_TABLE} SET return_value = ? '
                    'WHERE request_id = ?', ('x' * size, request_id))
    return ids


def _row_counts():
    """Return the (request, queue) row counts as another connection sees."""
    with sqlite3.connect(requests._DB.db_path) as conn:  # pylint: disable=protected-access
        return tuple(
            conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            for table in (requests.REQUEST_TABLE, 'task_queue'))


@pytest.mark.asyncio
async def test_batched_delete_removes_the_queue_entries_of_each_batch(
        isolated_database, monkeypatch):
    """Every batch commits its queue and request deletes together."""
    ids = await _seed_queued_requests(70)
    remaining = []

    async def pause(_):
        # A batch has committed. Its queue entries left with its requests.
        remaining.append(_row_counts())

    monkeypatch.setattr(requests.asyncio, 'sleep', pause)

    await requests.SqliteRequestBackend().delete_requests(ids)

    assert remaining == [(38, 38), (6, 6), (0, 0)]


@pytest.mark.asyncio
async def test_failed_queue_delete_keeps_the_request_rows(isolated_database):
    """A batch whose queue delete fails leaves its request rows in place."""
    ids = await _seed_queued_requests(2)
    with sqlite3.connect(requests._DB.db_path) as conn:  # pylint: disable=protected-access
        conn.execute('CREATE TRIGGER block_queue_delete '
                     'BEFORE DELETE ON task_queue '
                     "BEGIN SELECT RAISE(ABORT, 'blocked'); END")

    with pytest.raises(Exception, match='blocked'):
        await requests.SqliteRequestBackend().delete_requests(ids)

    assert _row_counts() == (2, 2)


@pytest.mark.asyncio
async def test_queue_entries_leave_with_the_requests_of_a_byte_split_batch(
        isolated_database, monkeypatch):
    """Byte-limited batches delete the queue entries of their own ids."""
    mib = 1024 * 1024
    ids = await _seed_queued_requests(3, payload_sizes=[9 * mib, 9 * mib, 1])
    remaining = []

    async def pause(_):
        remaining.append(_row_counts())

    monkeypatch.setattr(requests.asyncio, 'sleep', pause)

    await requests.SqliteRequestBackend().delete_requests(ids)

    assert remaining == [(2, 2), (1, 1), (0, 0)]


@pytest.mark.asyncio
@pytest.mark.parametrize('delete', [
    lambda: requests.SqliteRequestBackend().delete_requests(['removed-daemon']),
    lambda: requests.SqliteRequestBackend().
    delete_orphan_internal_daemons_async([_make_daemon('alpha-daemon')]),
],
                         ids=['delete_requests', 'delete_orphans'])
async def test_request_delete_waits_for_a_write_lock_without_blocking_the_loop(
        isolated_database, delete):
    for daemon_id in ['alpha-daemon', 'removed-daemon']:
        req = requests.build_internal_daemon_request(_make_daemon(daemon_id))
        assert await requests.create_if_not_exists_async(req)
    # Hold the write lock from another connection for a while.
    lock_conn = sqlite3.connect(requests._DB.db_path, isolation_level=None)  # pylint: disable=protected-access
    lock_conn.execute('BEGIN IMMEDIATE')
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    async def release_lock_later():
        await asyncio.sleep(0.5)
        lock_conn.execute('COMMIT')

    ticker_task = asyncio.create_task(ticker())
    release_task = asyncio.create_task(release_lock_later())
    try:
        await delete()
    finally:
        ticker_task.cancel()
        await release_task
        lock_conn.close()

    assert ticks >= 10


@pytest.mark.asyncio
@pytest.mark.parametrize('delete', [
    lambda: requests.SqliteRequestBackend().delete_requests(['removed-daemon']),
    lambda: requests.SqliteRequestBackend().
    delete_orphan_internal_daemons_async([_make_daemon('alpha-daemon')]),
],
                         ids=['delete_requests', 'delete_orphans'])
async def test_failed_request_delete_keeps_the_queue_entry(
        isolated_database, delete):
    """The queue delete and the request delete commit together or not at all."""
    for daemon_id in ['alpha-daemon', 'removed-daemon']:
        req = requests.build_internal_daemon_request(_make_daemon(daemon_id))
        assert await requests.create_if_not_exists_async(req)
        await queue_base.DbQueueBackend(req.schedule_type.value).put_async(
            (daemon_id, False, True))
    # Make the request-row delete fail after the queue delete has run.
    requests._DB.conn.execute(
        f'CREATE TRIGGER block_delete BEFORE DELETE ON {requests.REQUEST_TABLE}'
        " BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    requests._DB.conn.commit()

    with pytest.raises(Exception, match='blocked'):
        await delete()

    assert _queued_ids() == ['alpha-daemon', 'removed-daemon']


@pytest.mark.asyncio
async def test_refreshing_a_daemon_row_does_not_enqueue_it_again(
        isolated_database):
    """Two submissions of one daemon leave exactly one queue entry."""
    executor._get_queue.cache_clear()  # pylint: disable=protected-access
    try:
        daemon = _make_daemon('alpha-daemon')
        await executor.schedule_internal_daemon_async(daemon)
        await executor.schedule_internal_daemon_async(daemon)

        assert _queued_ids() == ['alpha-daemon']
    finally:
        executor._get_queue.cache_clear()  # pylint: disable=protected-access

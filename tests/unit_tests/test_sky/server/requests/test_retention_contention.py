"""SQLite retention query and transaction budgets on the native schema."""
import sqlite3
from unittest import mock

import pytest
import pytest_asyncio

from sky.server.requests import requests
from sky.utils.db import db_utils


@pytest_asyncio.fixture(name='retention')
async def retention_database(tmp_path, monkeypatch):
    db = db_utils.SQLiteConn(str(tmp_path / 'requests.db'),
                             requests.create_table)
    monkeypatch.setattr(requests, '_DB', db)
    backend = requests.SqliteRequestBackend()
    monkeypatch.setattr(requests.request_storage, 'get_request_backend',
                        lambda: backend)
    monkeypatch.setattr(requests.server_constants, 'REQUEST_LOG_PATH_PREFIX',
                        str(tmp_path / 'logs'))
    monkeypatch.setattr(requests, 'LEGACY_REQUEST_LOG_PATH_PREFIX',
                        str(tmp_path / 'legacy'))
    monkeypatch.setattr(requests.sky_logging, 'DEBUG_LOG_DIR',
                        str(tmp_path / 'debug'))
    yield db, backend
    await db.close()


def insert(db,
           request_id,
           *,
           size=0,
           status='SUCCEEDED',
           finished_at=1.,
           column='return_value'):
    with db.conn:
        db.conn.execute(
            f'INSERT INTO requests '
            f'(request_id, status, finished_at, {column}) VALUES (?, ?, ?, ?)',
            (request_id, status, finished_at, 'x' * size))


@pytest.mark.asyncio
async def test_retention_index_migrates_and_covers_selection(retention):
    db, _ = retention
    db.conn.execute('DROP INDEX IF EXISTS requests_retention_idx')
    insert(db, 'expired', size=1024 * 1024)
    query, params = requests.RequestTaskFilter(
        status=requests.RequestStatus.finished_status(),
        finished_before=100.,
        limit=100,
        fields=['request_id']).build_query()
    requests.create_table(db.conn.cursor(), db.conn)
    requests.create_table(db.conn.cursor(), db.conn)
    plan = str(
        db.conn.execute('EXPLAIN QUERY PLAN ' + query, params).fetchall())
    assert 'USING COVERING INDEX requests_retention_idx' in plan
    assert db.conn.execute(query, params).fetchall() == [('expired',)]
    assert db.conn.execute(
        'SELECT length(return_value) FROM requests').fetchone() == (1024 *
                                                                    1024,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'column', ['return_value', 'request_body', 'error', 'entrypoint'])
async def test_delete_budgets_and_post_commit_writer(retention, monkeypatch,
                                                     column):
    db, backend = retention
    mib = 1024 * 1024
    sizes = [9 * mib, 5 * mib, 5 * mib, *([10] * 40)]
    ids = [f'expired-{index:03}' for index in range(len(sizes))]
    for request_id, size in zip(ids, sizes):
        insert(db, request_id, size=size, column=column)
    sizes_by_id = dict(zip(ids, sizes))
    commits = []
    pauses = []
    original = db.execute_and_commit_async

    async def commit(sql, parameters=None):
        await original(sql, parameters)
        commits.append(list(parameters))

    async def pause(seconds):
        pauses.append(seconds)
        # A separate connection must be able to write during every pause.
        with sqlite3.connect(db.db_path, timeout=0) as other:
            other.execute(
                'INSERT OR REPLACE INTO requests (request_id) VALUES (?)',
                ('admitted',))

    monkeypatch.setattr(db, 'execute_and_commit_async', commit)
    monkeypatch.setattr(requests.asyncio, 'sleep', pause)
    await backend.delete_requests(ids + ['already-absent'])
    deleted = [rid for batch in commits for rid in batch]
    assert sorted(deleted) == sorted(ids)
    assert len(commits) == len(pauses) > 2
    assert all(delay > 0 for delay in pauses)
    for batch in commits:
        assert len(batch) <= 32
        assert sum(sizes_by_id[rid]
                   for rid in batch) <= 8 * mib or len(batch) == 1
    assert db.conn.execute('SELECT request_id FROM requests').fetchall() == [
        ('admitted',)
    ]
    await backend.delete_requests([])
    await backend.delete_requests(ids)
    assert len(commits) == len(pauses)


@pytest.mark.asyncio
async def test_cleanup_fixed_cutoff_and_log_order(retention, monkeypatch,
                                                  tmp_path):
    db, _ = retention
    monkeypatch.setattr(requests.time, 'time', lambda: 100.)
    expired = ('SUCCEEDED', 'FAILED', 'CANCELLED')
    preserved = {'active', 'pending', 'null-finished', 'recent', 'boundary'}
    for status in expired:
        insert(db, status, status=status)
    for name, status, finished_at in (('active', 'RUNNING',
                                       1.), ('pending', 'PENDING', None),
                                      ('null-finished', 'SUCCEEDED',
                                       None), ('recent', 'SUCCEEDED', 95.),
                                      ('boundary', 'SUCCEEDED', 90.)):
        insert(db, name, status=status, finished_at=finished_at)
    for directory in ('logs', 'legacy', 'debug'):
        path = tmp_path / directory
        path.mkdir()
        for name in (*expired, *preserved):
            (path / f'{name}.log').touch()
    original = requests._delete_requests  # pylint: disable=protected-access

    async def delete(ids):
        for request_id in ids:
            assert not any(
                (tmp_path / directory / f'{request_id}.log').exists()
                for directory in ('logs', 'legacy', 'debug'))
        monkeypatch.setattr(requests.time, 'time', lambda: 1000.)
        await original(ids)

    monkeypatch.setattr(requests, '_delete_requests', delete)
    await requests.clean_finished_requests_with_retention(10, batch_size=1)
    assert {
        row[0] for row in db.conn.execute('SELECT request_id FROM requests')
    } == preserved
    for directory in ('logs', 'legacy', 'debug'):
        for name in preserved:
            assert (tmp_path / directory / f'{name}.log').exists()


@pytest.mark.asyncio
async def test_log_cleanup_failure_preserves_row(retention, monkeypatch):
    db, _ = retention
    insert(db, 'expired')
    monkeypatch.setattr(requests.anyio.Path, 'unlink',
                        mock.AsyncMock(side_effect=PermissionError('denied')))
    with pytest.raises(PermissionError):
        await requests.clean_finished_requests_with_retention(0)
    assert db.conn.execute('SELECT request_id FROM requests').fetchall() == [
        ('expired',)
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures('retention')
@pytest.mark.parametrize('batch_size', [0, -1])
async def test_invalid_batch_size(batch_size):
    with pytest.raises(ValueError, match='positive'):
        await requests.clean_finished_requests_with_retention(
            0, batch_size=batch_size)

"""Tests for the request queue stored in the request database."""

import asyncio
import contextlib
import multiprocessing
import sqlite3
import threading
from unittest import mock

import pytest

from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server.requests.queues import base
from sky.server.requests.queues import db_queue


@pytest.fixture()
def db_path(tmp_path):
    """A request database file private to one test."""
    path = str(tmp_path / 'requests.db')
    with mock.patch.object(server_constants, 'API_SERVER_REQUEST_DB_PATH',
                           path):
        yield path


def _patch_metric(monkeypatch):
    """Enable metrics and swap in a mock histogram; return the mock."""
    monkeypatch.setattr(base.metrics_utils, 'METRICS_ENABLED', True)
    mock_hist = mock.MagicMock()
    monkeypatch.setattr(base.metrics_utils, 'SKY_APISERVER_QUEUE_WAIT_SECONDS',
                        mock_hist)
    return mock_hist


@contextlib.contextmanager
def _pinned_clock(times):
    """Makes the queue read the given times, in order, as the current time.

    Only the queue modules read the pinned clock, so logging and the
    database driver keep the real one.
    """
    fake_time = mock.Mock()
    fake_time.time.side_effect = times
    with mock.patch.object(db_queue, 'time', fake_time):
        with mock.patch.object(base, 'time', fake_time):
            yield


class _BarrierConn:
    """A connection that makes every claimer peek before any claimer deletes.

    After its first peek, a claimer waits for the other claimer to finish its
    own first peek.
    """

    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier
        self.waited = False

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *args):
        return self._conn.__exit__(*args)

    def cursor(self):
        return _BarrierCursor(self._conn.cursor(), self)


class _BarrierCursor:

    def __init__(self, cursor, owner):
        self._cursor = cursor
        self._owner = owner

    def execute(self, sql, *args):
        self._cursor.execute(sql, *args)
        # Only the first peek waits. A claimer that lost the race peeks again.
        if (sql == db_queue._PEEK_SQL and  # pylint: disable=protected-access
                not self._owner.waited):
            self._owner.waited = True
            self._owner._barrier.wait(timeout=60)  # pylint: disable=protected-access
        return self

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _claim_between_peek_and_delete(path, barrier, results):
    server_constants.API_SERVER_REQUEST_DB_PATH = path
    backend = base.DbQueueBackend('long')
    backend._db.conn = _BarrierConn(backend._db.conn, barrier)  # pylint: disable=protected-access
    results.put(backend.get())


def _put_in_new_process(path, item):
    server_constants.API_SERVER_REQUEST_DB_PATH = path
    base.DbQueueBackend('long').put(item)


def test_two_processes_claim_one_row_and_one_wins(db_path):
    item = ('req-1', False, True)
    base.DbQueueBackend('long').put(item)

    ctx = multiprocessing.get_context('spawn')
    barrier = ctx.Barrier(2)
    results = ctx.Queue()
    procs = [
        ctx.Process(target=_claim_between_peek_and_delete,
                    args=(db_path, barrier, results)) for _ in range(2)
    ]
    for proc in procs:
        proc.start()
    claimed = [results.get(timeout=120) for _ in procs]
    for proc in procs:
        proc.join(timeout=60)

    assert sorted(claimed, key=lambda c: c is None) == [item, None]
    assert base.DbQueueBackend('long').qsize() == 0


def test_get_returns_entries_in_put_order(db_path):
    backend = base.DbQueueBackend('long')
    for i in range(5):
        backend.put((f'req-{i}', False, False))

    assert [backend.get()[0] for _ in range(5)
           ] == [f'req-{i}' for i in range(5)]
    assert backend.get() is None


def test_put_again_goes_to_the_back(db_path):
    backend = base.DbQueueBackend('long')
    for request_id in ['req-a', 'req-b', 'req-c']:
        backend.put((request_id, False, False))

    first = backend.get()
    backend.put(first)

    assert [backend.get()[0] for _ in range(3)] == ['req-b', 'req-c', 'req-a']


def test_schedule_types_do_not_share_entries(db_path):
    long_queue = base.DbQueueBackend('long')
    short_queue = base.DbQueueBackend('short')
    long_queue.put(('req-long', False, False))
    short_queue.put(('req-short', False, False))

    assert short_queue.qsize() == 1
    assert short_queue.get() == ('req-short', False, False)
    assert short_queue.get() is None
    assert long_queue.get() == ('req-long', False, False)


@pytest.mark.parametrize('ignore_return_value', [False, True])
@pytest.mark.parametrize('retryable', [False, True])
def test_flags_round_trip(db_path, ignore_return_value, retryable):
    backend = base.DbQueueBackend('long')
    item = ('req-flags', ignore_return_value, retryable)

    backend.put(item)

    assert backend.get() == item


def test_async_put_is_visible_to_get(db_path):
    backend = base.DbQueueBackend('short')
    item = ('req-async', True, False)

    asyncio.run(backend.put_async(item))

    assert backend.get() == item


def test_new_process_puts_and_this_process_gets(db_path):
    item = ('req-child', True, True)
    ctx = multiprocessing.get_context('spawn')
    proc = ctx.Process(target=_put_in_new_process, args=(db_path, item))
    proc.start()
    proc.join(timeout=120)
    assert proc.exitcode == 0

    assert base.DbQueueBackend('long').get() == item


def test_wait_metric_measures_time_since_put(db_path, monkeypatch):
    mock_hist = _patch_metric(monkeypatch)
    backend = base.DbQueueBackend('short')
    clock = _pinned_clock([100.0, 107.5])

    with clock:
        backend.put(('req-wait', False, False))
        backend.get()

    mock_hist.labels.assert_called_once_with(schedule_type='short')
    mock_hist.labels.return_value.observe.assert_called_once_with(7.5)


def test_put_again_measures_only_the_new_wait(db_path, monkeypatch):
    mock_hist = _patch_metric(monkeypatch)
    backend = base.DbQueueBackend('long')
    clock = _pinned_clock([100.0, 400.0, 401.0, 403.0])
    item = ('req-again', False, True)

    with clock:
        backend.put(item)
        backend.get()
        backend.put(item)
        backend.get()

    observed = [
        c[0][0] for c in mock_hist.labels.return_value.observe.call_args_list
    ]
    assert observed == [300.0, 2.0]


def test_no_metric_when_metrics_are_disabled(db_path, monkeypatch):
    monkeypatch.setattr(base.metrics_utils, 'METRICS_ENABLED', False)
    mock_hist = mock.MagicMock()
    monkeypatch.setattr(base.metrics_utils, 'SKY_APISERVER_QUEUE_WAIT_SECONDS',
                        mock_hist)
    backend = base.DbQueueBackend('long')

    backend.put(('req-quiet', False, False))

    assert backend.get() == ('req-quiet', False, False)
    mock_hist.labels.assert_not_called()


def test_empty_get_does_not_wait_for_a_held_write_lock(db_path):
    backend = base.DbQueueBackend('long')
    writer = sqlite3.connect(db_path, isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    result = []
    poller = threading.Thread(target=lambda: result.append(backend.get()),
                              daemon=True)
    try:
        poller.start()
        poller.join(timeout=10)
        finished = not poller.is_alive()
    finally:
        # Release the lock so a poller that blocked can end.
        writer.execute('ROLLBACK')
        writer.close()
        poller.join(timeout=120)

    assert finished, 'An empty get() waited for the write lock.'
    assert result == [None]


def test_clearing_the_database_empties_the_queue(db_path):
    base.DbQueueBackend('long').put(('req-old', False, False))

    server_common.clear_local_api_server_database()

    restarted = base.DbQueueBackend('long')
    assert restarted.qsize() == 0
    assert restarted.get() is None

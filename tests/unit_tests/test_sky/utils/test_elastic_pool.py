"""Tests for sky.utils.db.elastic_pool."""
import gc
import time
from typing import List
from unittest import mock
import weakref

import pytest
import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc

from sky.utils.db import elastic_pool

IDLE = 1000.0


class _Creator:
    """DBAPI connection factory that records every connection it opens."""

    def __init__(self):
        self.opened: List[mock.MagicMock] = []

    def __call__(self):
        conn = mock.MagicMock(name=f'conn{len(self.opened)}')
        self.opened.append(conn)
        return conn


def _pool(creator, size=3, min_size=1, idle_timeout=IDLE, timeout=30.0):
    return elastic_pool.ElasticQueuePool(creator,
                                         pool_size=size,
                                         min_size=min_size,
                                         idle_timeout=idle_timeout,
                                         timeout=timeout)


def _open(pool) -> int:
    return pool.checkedin() + pool.checkedout()


def _use(pool, count: int) -> None:
    held = [pool.connect() for _ in range(count)]
    for conn in held:
        conn.close()


def test_returned_connections_stay_pooled():
    creator = _Creator()
    pool = _pool(creator)
    _use(pool, 3)
    assert _open(pool) == 3
    _use(pool, 3)
    assert len(creator.opened) == 3
    assert not any(c.close.called for c in creator.opened)


def test_checkout_beyond_pool_size_waits_then_times_out():
    pool = _pool(_Creator(), size=1, min_size=0, timeout=0.2)
    held = pool.connect()
    start = time.monotonic()
    with pytest.raises(sqlalchemy_exc.TimeoutError):
        pool.connect()
    assert 0.15 <= time.monotonic() - start < 2
    held.close()


def test_reap_closes_idle_connections_down_to_min_size():
    creator = _Creator()
    pool = _pool(creator, size=3, min_size=1)
    _use(pool, 3)
    now = time.monotonic()
    assert pool.reap_idle(now + IDLE / 2) == 0
    assert pool.reap_idle(now + IDLE + 1) == 2
    assert _open(pool) == 1
    assert sum(c.close.called for c in creator.opened) == 2


def test_reap_keeps_recently_used_connections():
    creator = _Creator()
    pool = _pool(creator, size=3, min_size=0)
    _use(pool, 3)
    returned_at = time.monotonic()
    # LIFO hands back the most recently returned connection, so steady
    # single-threaded use keeps reusing one connection.
    with mock.patch.object(time,
                           'monotonic',
                           return_value=returned_at + IDLE / 2):
        pool.connect().close()
    assert pool.reap_idle(returned_at + IDLE + 1) == 2
    assert _open(pool) == 1
    assert [c.close.called for c in creator.opened] == [True, True, False]


def test_reaped_slots_open_again_on_demand():
    creator = _Creator()
    pool = _pool(creator, size=2, min_size=0)
    _use(pool, 2)
    assert pool.reap_idle(time.monotonic() + IDLE + 1) == 2
    assert _open(pool) == 0
    held = [pool.connect() for _ in range(2)]
    assert len(creator.opened) == 4
    assert _open(pool) == 2
    for conn in held:
        conn.close()


def test_reap_after_dispose_keeps_the_count_consistent():
    pool = _pool(_Creator(), size=2, min_size=0)
    _use(pool, 2)
    pool.dispose()
    assert pool.reap_idle(time.monotonic() + IDLE + 1) == 0
    assert _open(pool) == 0
    held = [pool.connect() for _ in range(2)]
    assert _open(pool) == 2
    for conn in held:
        conn.close()


def test_recreate_keeps_the_limits():
    pool = _pool(_Creator(), size=4, min_size=2, idle_timeout=7.0, timeout=3.0)
    new_pool = pool.recreate()
    assert isinstance(new_pool, elastic_pool.ElasticQueuePool)
    assert new_pool.size() == 4
    assert new_pool.timeout() == 3.0
    assert new_pool._min_size == 2
    assert new_pool._idle_timeout == 7.0
    assert new_pool._max_overflow == 0


def test_create_engine_passes_the_pool_arguments():
    engine = sqlalchemy.create_engine('sqlite://',
                                      poolclass=elastic_pool.ElasticQueuePool,
                                      pool_size=4,
                                      min_size=2,
                                      idle_timeout=9.0,
                                      pool_timeout=5.0)
    pool = engine.pool
    assert isinstance(pool, elastic_pool.ElasticQueuePool)
    assert pool.size() == 4
    assert pool._max_overflow == 0
    assert pool._min_size == 2
    assert pool._idle_timeout == 9.0
    assert pool.timeout() == 5.0
    engine.dispose()


@pytest.mark.parametrize('kwargs', [
    {
        'pool_size': 0
    },
    {
        'pool_size': 2,
        'min_size': 3
    },
    {
        'pool_size': 2,
        'min_size': -1
    },
    {
        'pool_size': 2,
        'idle_timeout': 0
    },
])
def test_invalid_limits_are_refused(kwargs):
    with pytest.raises(ValueError):
        elastic_pool.ElasticQueuePool(_Creator(), **kwargs)


def test_reaper_does_not_keep_the_pool_alive():
    pool_ref = weakref.ref(_pool(_Creator()))
    # The reaper holds the pool only while it reaps, a moment after start.
    deadline = time.monotonic() + 5
    while pool_ref() is not None and time.monotonic() < deadline:
        gc.collect()
        time.sleep(0.01)
    assert pool_ref() is None
    # Returns instead of sleeping forever.
    elastic_pool._reap_forever(pool_ref)

"""Status refresh must not start more threads than the DB pool can serve."""
import contextlib
from unittest import mock

import sqlalchemy

from sky.backends import backend_utils

_POSTGRES_URL = 'postgresql+psycopg2://sky@localhost/sky'


def _queue_pool_engine(pool_size: int,
                       max_overflow: int) -> sqlalchemy.engine.Engine:
    # Creating an engine does not connect, so no database is needed.
    return sqlalchemy.create_engine(_POSTGRES_URL,
                                    poolclass=sqlalchemy.pool.QueuePool,
                                    pool_size=pool_size,
                                    max_overflow=max_overflow)


@contextlib.contextmanager
def _engines(state_engine, lock_engine=None, max_overflow=0, cpu_threads=31):
    with mock.patch(
            'sky.backends.backend_utils.global_user_state.'
            'initialize_and_get_db',
            return_value=state_engine), mock.patch(
                'sky.backends.backend_utils.locks.postgres_lock_engine',
                return_value=lock_engine or state_engine), mock.patch(
                    'sky.utils.db.db_utils.'
                    'get_db_connection_pool_max_overflow',
                    return_value=max_overflow) as overflow, mock.patch(
                        'sky.backends.backend_utils.subprocess_utils.'
                        'get_parallel_threads',
                        return_value=cpu_threads):
        yield overflow


def test_null_pool_keeps_the_cpu_default_even_with_a_connection_budget():
    # Default config: the engine was cached as NullPool at import, before
    # set_max_connections() gave the process a budget. NullPool never queues.
    engine = sqlalchemy.create_engine(_POSTGRES_URL,
                                      poolclass=sqlalchemy.NullPool)
    with _engines(engine, max_overflow=4):
        assert backend_utils._status_refresh_num_threads() is None


def test_sqlite_keeps_the_cpu_default():
    with _engines(sqlalchemy.create_engine('sqlite://')):
        assert backend_utils._status_refresh_num_threads() is None


def test_threads_leave_room_for_a_query_beside_the_lock():
    # pool 1 + overflow 9 is 10 connections. Each refresh holds the lock and
    # may check out one more connection, so five threads fill the pool.
    with _engines(_queue_pool_engine(1, 9), max_overflow=9) as overflow:
        assert backend_utils._status_refresh_num_threads() == 5
    overflow.assert_called_once_with(1)


def test_lock_on_its_own_engine_uses_the_whole_pool():
    # Behind a pooler the lock connects directly, outside the state pool, so
    # every refresh thread needs only a short state-query checkout.
    direct = sqlalchemy.create_engine(_POSTGRES_URL,
                                      poolclass=sqlalchemy.NullPool)
    with _engines(_queue_pool_engine(1, 9), lock_engine=direct, max_overflow=9):
        assert backend_utils._status_refresh_num_threads() == 10


def test_large_pool_stays_at_the_cpu_default():
    with _engines(_queue_pool_engine(40, 40), max_overflow=40):
        assert backend_utils._status_refresh_num_threads() == 31


def test_a_single_connection_still_refreshes_one_cluster_at_a_time():
    with _engines(_queue_pool_engine(1, 0)):
        assert backend_utils._status_refresh_num_threads() == 1


def test_refresh_passes_the_pool_cap_to_the_thread_pool():
    with mock.patch(
            'sky.backends.backend_utils.global_user_state.get_cluster_names',
            return_value={'alpha', 'beta'}), mock.patch(
                'sky.backends.backend_utils.requests_lib.get_request_tasks',
                return_value=[]), mock.patch(
                    'sky.backends.backend_utils._status_refresh_num_threads',
                    return_value=5), mock.patch(
                        'sky.backends.backend_utils.subprocess_utils.'
                        'run_in_parallel') as run:
        backend_utils.refresh_cluster_records()

    run.assert_called_once()
    assert run.call_args.kwargs['num_threads'] == 5
    assert set(run.call_args.args[1]) == {'alpha', 'beta'}

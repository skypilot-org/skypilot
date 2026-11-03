"""Status refresh must not start more threads than the DB pool can serve."""
from unittest import mock

from sky.backends import backend_utils


def test_null_pool_keeps_the_cpu_default():
    with mock.patch('sky.utils.db.db_utils.get_db_connection_pool_size',
                    return_value=0):
        assert backend_utils._status_refresh_num_threads() is None


def test_threads_leave_room_for_a_query_beside_the_lock():
    # pool 1 + overflow 9 is 10 connections. Each refresh holds the lock and
    # may check out one more connection, so five threads fill the pool.
    with mock.patch('sky.utils.db.db_utils.get_db_connection_pool_size',
                    return_value=1), mock.patch(
                        'sky.utils.db.db_utils.'
                        'get_db_connection_pool_max_overflow',
                        return_value=9) as overflow, mock.patch(
                            'sky.backends.backend_utils.subprocess_utils.'
                            'get_parallel_threads',
                            return_value=31):
        assert backend_utils._status_refresh_num_threads() == 5
        overflow.assert_called_once_with(1)


def test_large_pool_stays_at_the_cpu_default():
    with mock.patch('sky.utils.db.db_utils.get_db_connection_pool_size',
                    return_value=40), mock.patch(
                        'sky.utils.db.db_utils.'
                        'get_db_connection_pool_max_overflow',
                        return_value=40), mock.patch(
                            'sky.backends.backend_utils.subprocess_utils.'
                            'get_parallel_threads',
                            return_value=31):
        assert backend_utils._status_refresh_num_threads() == 31


def test_a_single_connection_still_refreshes_one_cluster_at_a_time():
    with mock.patch('sky.utils.db.db_utils.get_db_connection_pool_size',
                    return_value=1), mock.patch(
                        'sky.utils.db.db_utils.'
                        'get_db_connection_pool_max_overflow',
                        return_value=0):
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

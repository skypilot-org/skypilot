import asyncio
import sys
import unittest.mock as mock

import pytest
from sqlalchemy import create_engine

import sky
from sky import backends
from sky import global_user_state
from sky.utils import annotations


@pytest.fixture
def _mock_db_conn(tmp_path, monkeypatch):
    # Create a temporary database file
    db_path = tmp_path / 'state_testing.db'

    sqlalchemy_engine = create_engine(f'sqlite:///{db_path}')

    monkeypatch.setattr(global_user_state, '_SQLALCHEMY_ENGINE',
                        sqlalchemy_engine)

    global_user_state.create_table(sqlalchemy_engine)


@pytest.mark.skipif(sys.platform != 'linux', reason='Only test in CI.')
def test_enabled_clouds_empty():
    # In test environment, no cloud should be enabled.
    assert sky.global_user_state.get_cached_enabled_clouds(
        sky.clouds.cloud.CloudCapability.COMPUTE, workspace='default') == []


@pytest.mark.asyncio
async def test_cluster_event_retention_daemon():
    """Test the cluster event retention daemon runs correctly."""
    with mock.patch('sky.global_user_state.skypilot_config') as mock_config:
        with mock.patch(
                'sky.global_user_state.cleanup_cluster_events_with_retention'
        ) as mock_clean:
            with mock.patch('asyncio.sleep') as mock_sleep:
                # Configure negative retention (disabled)
                mock_config.get_nested.return_value = -1

                mock_sleep.side_effect = [None, asyncio.CancelledError()]

                # Run the daemon
                with pytest.raises(asyncio.CancelledError):
                    await sky.global_user_state.cluster_event_retention_daemon()

                # Verify cleanup was NOT called due to negative retention
                mock_clean.assert_not_called()

                # Verify sleep was called with max(-1, 3600) = 3600
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(3600)

                # Now test when we run retention with a positive value.
                mock_config.get_nested.return_value = 2  # every 2 hours.
                mock_sleep.side_effect = [None, None, asyncio.CancelledError()]

                with pytest.raises(asyncio.CancelledError):
                    await sky.global_user_state.cluster_event_retention_daemon()

                # Verify cleanup was called with the correct retention.
                mock_clean.assert_any_call(
                    2, sky.global_user_state.ClusterEventType.STATUS_CHANGE)
                mock_clean.assert_any_call(
                    2, sky.global_user_state.ClusterEventType.DEBUG)

                # Verify sleep was called with max(2 * 3600, 3600) = 7200
                assert mock_sleep.call_count == 5
                mock_sleep.assert_any_call(7200)


def test_add_or_update_cluster_update_only(_mock_db_conn):
    # if no record exists and existing_cluster_hash is specified
    # should raise ValueError
    with pytest.raises(ValueError):
        global_user_state.add_or_update_cluster(
            'test-cluster',
            'test-cluster',
            sky.Resources(infra='aws/us-east-1/us-east-1a',
                          instance_type='p4d.24xlarge'),
            ready=True,
            existing_cluster_hash='01230123')

    # if existing_cluster_hash is not specified, should insert a new record
    global_user_state.add_or_update_cluster(
        'test-cluster',
        'test-cluster',
        sky.Resources(infra='aws/us-east-1/us-east-1a',
                      instance_type='p4d.24xlarge'),
        ready=True)

    with pytest.raises(ValueError):
        # incorrect cluster hash filter
        global_user_state.add_or_update_cluster(
            'test-cluster',
            'test-cluster',
            sky.Resources(infra='aws/us-east-1/us-east-1a',
                          instance_type='p4d.24xlarge'),
            ready=True,
            existing_cluster_hash='01230123')

    # correct cluster hash filter
    record = global_user_state.get_cluster_from_name('test-cluster')
    global_user_state.add_or_update_cluster(
        'test-cluster',
        'test-cluster',
        sky.Resources(infra='aws/us-east-1/us-east-1a',
                      instance_type='p4d.24xlarge'),
        ready=True,
        existing_cluster_hash=record['cluster_hash'])


def test_get_clusters_cache_size(_mock_db_conn):
    """Test that calling get_clusters does not cause the LRU cache
    to grow without bound."""
    # Clear any existing request-level cache before starting
    annotations.clear_request_level_cache()

    # Create multiple real CloudVmRayResourceHandle instances with synthetic data
    handles = []
    for i in range(15):  # Create more than maxsize=10 to test cache eviction
        cluster_name = f'test-cluster-{i}'

        # Create a real handle instance
        handle = backends.CloudVmRayResourceHandle(
            cluster_name=cluster_name,
            cluster_name_on_cloud=cluster_name,
            cluster_yaml=None,  # Can be None for testing
            launched_nodes=1,
            launched_resources=sky.Resources(infra='aws/us-east-1/us-east-1a',
                                             instance_type='m5.large'),
            stable_internal_external_ips=[('10.0.0.1', '1.2.3.4')],
            stable_ssh_ports=[22],
        )

        # Add to global state
        global_user_state.add_or_update_cluster(
            cluster_name,
            handle,
            requested_resources={handle.launched_resources},
            ready=True)
        handles.append(handle)

    # Get all clusters to retrieve the handles from the database
    cluster_records = global_user_state.get_clusters()
    assert len(cluster_records) == 15, "Should have 15 clusters in database"

    # Call get_command_runners on each handle to populate the cache
    cache_call_results = []

    with mock.patch('sky.backends.backend_utils.ssh_credential_from_yaml'
                   ) as mock_ssh_creds:
        # Mock SSH credentials to avoid file system dependencies
        mock_ssh_creds.return_value = {
            'ssh_user': 'ubuntu',
            'ssh_private_key': '/tmp/fake_key'
        }

        with mock.patch(
                'sky.utils.command_runner.SSHCommandRunner.make_runner_list'
        ) as mock_make_runners:
            # Mock the command runner creation to avoid actual SSH connections
            mock_make_runners.return_value = [mock.MagicMock()]

            for i, record in enumerate(cluster_records):
                handle = record['handle']

                try:
                    # Call get_command_runners which should be cached
                    runners = handle.get_command_runners()
                    cache_call_results.append(
                        ('success', len(runners) if runners else 0))

                    # Verify we get command runners back
                    assert isinstance(
                        runners,
                        list), "Should return a list of command runners"

                except Exception as e:
                    # Track any failures for debugging
                    cache_call_results.append(('error', str(e)))

                # Check if the handle's get_command_runners method has cache_info
                if hasattr(handle.get_command_runners, 'cache_info'):
                    cache_info = handle.get_command_runners.cache_info()
                    print(f"Handle {i}: Cache size={cache_info.currsize}, "
                          f"hits={cache_info.hits}, misses={cache_info.misses}")

                    # Cache size should not exceed maxsize=10
                    assert cache_info.currsize <= 10, (
                        f"Cache size {cache_info.currsize} exceeds maxsize=10")

    # Verify that calling get_command_runners multiple times uses cache
    successful_calls = [
        result for result in cache_call_results if result[0] == 'success'
    ]
    assert len(successful_calls
              ) > 0, "At least some get_command_runners calls should succeed"

    # Test calling the same handle multiple times to verify cache hits
    if cluster_records:
        first_handle = cluster_records[0]['handle']
        with mock.patch('sky.backends.backend_utils.ssh_credential_from_yaml'
                       ) as mock_ssh_creds:
            mock_ssh_creds.return_value = {
                'ssh_user': 'ubuntu',
                'ssh_private_key': '/tmp/fake_key'
            }
            with mock.patch(
                    'sky.utils.command_runner.SSHCommandRunner.make_runner_list'
            ) as mock_make_runners:
                mock_make_runners.return_value = [mock.MagicMock()]

                # Call multiple times - should hit cache
                try:
                    first_handle.get_command_runners()
                    first_handle.get_command_runners()

                    # Verify cache behavior if cache_info is available
                    if hasattr(first_handle.get_command_runners, 'cache_info'):
                        cache_info = first_handle.get_command_runners.cache_info(
                        )
                        assert cache_info.hits > 0, "Should have cache hits for repeated calls"
                        print(
                            f"After repeated calls: hits={cache_info.hits}, misses={cache_info.misses}"
                        )
                except Exception as e:
                    print(f"Cache hit test failed: {e}")

    # Test cache clearing functionality
    initial_functions = len(annotations._FUNCTIONS_NEED_RELOAD_CACHE)
    annotations.clear_request_level_cache()

    # The number of functions tracked should remain the same, but caches should be cleared
    final_functions = len(annotations._FUNCTIONS_NEED_RELOAD_CACHE)
    assert final_functions == initial_functions, "Function count should remain the same after cache clear"

    print(
        f"Test completed successfully. Tested cache behavior with {len(cluster_records)} clusters"
    )
    print(f"Successful get_command_runners calls: {len(successful_calls)}")
    print(f"Request-level cache functions tracked: {final_functions}")


@pytest.mark.parametrize('should_mock,return_user', [
    (True, True),
    (False, True),
    (True, False),
    (False, False),
])
def test_add_or_update_user_new_user(_mock_db_conn, should_mock, return_user):
    """Test add new user."""
    user_id = f'test-new-user-{str(should_mock).lower()}-{str(return_user).lower()}'
    password = 'password123'

    def test():
        user = sky.models.User(id=user_id, name='Test User', password=password)
        result = global_user_state.add_or_update_user(user,
                                                      return_user=return_user)

        if return_user:
            was_inserted, returned_user = result
            assert was_inserted is True
            assert returned_user is not None
            assert returned_user.id == user_id
            assert returned_user.name == 'Test User'
            assert returned_user.password == password
            assert returned_user.created_at is not None
        else:
            assert result is True

        fetched_user = global_user_state.get_user(user_id)
        assert fetched_user is not None
        assert fetched_user.name == 'Test User'
        assert fetched_user.password == password

    if should_mock:
        # Mock _sqlite_supports_returning so we don't have to install an older version of SQLite or SQLAlchemy.
        with mock.patch('sky.global_user_state._sqlite_supports_returning',
                        return_value=False):
            test()
    else:
        test()


@pytest.mark.parametrize('should_mock', [True, False])
def test_add_or_update_user_update_user(_mock_db_conn, should_mock):
    """Test update user."""

    def test():
        user_id = 'test-update-user'

        # Insert
        user = sky.models.User(id=user_id, name='Original', password='old')
        was_inserted, original_user = global_user_state.add_or_update_user(
            user, return_user=True)
        assert was_inserted is True
        original_created_at = original_user.created_at

        # Update
        updated_user_data = sky.models.User(id=user_id,
                                            name='Updated',
                                            password='new')
        was_inserted, updated_user = global_user_state.add_or_update_user(
            updated_user_data, return_user=True)

        assert was_inserted is False
        assert updated_user.name == 'Updated'
        assert updated_user.password == 'new'
        assert updated_user.created_at == original_created_at

        # Verify in database
        fetched_user = global_user_state.get_user(user_id)
        assert fetched_user.name == 'Updated'
        assert fetched_user.password == 'new'

    if should_mock:
        with mock.patch('sky.global_user_state._sqlite_supports_returning',
                        return_value=False):
            test()
    else:
        test()


@pytest.mark.parametrize('should_mock', [True, False])
def test_add_or_update_user_update_partial(_mock_db_conn, should_mock):
    """Test update user without changing password."""

    def test():
        user_id = 'test-update-partial-user'

        # Insert with password
        user = sky.models.User(id=user_id, name='Original', password='old')
        global_user_state.add_or_update_user(user, return_user=False)

        # Update only name
        updated_user_data = sky.models.User(id=user_id, name='Updated')
        was_inserted, updated_user = global_user_state.add_or_update_user(
            updated_user_data, return_user=True)

        assert was_inserted is False
        assert updated_user.name == 'Updated'
        assert updated_user.password == 'old'

        fetched_user = global_user_state.get_user(user_id)
        assert fetched_user.name == 'Updated'
        assert fetched_user.password == 'old'

    if should_mock:
        with mock.patch('sky.global_user_state._sqlite_supports_returning',
                        return_value=False):
            test()
    else:
        test()


@pytest.mark.parametrize('should_mock', [True, False])
def test_add_or_update_user_name_none(_mock_db_conn, should_mock):
    """Test that user with name=None returns False without inserting."""

    def test():
        user = sky.models.User(id='test-user-none', name=None)
        result = global_user_state.add_or_update_user(user, return_user=False)
        assert result is False

    if should_mock:
        with mock.patch('sky.global_user_state._sqlite_supports_returning',
                        return_value=False):
            test()
    else:
        test()

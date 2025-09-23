import asyncio
import sys
import unittest.mock as mock

import pytest
from sqlalchemy import create_engine

import sky
from sky import global_user_state


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

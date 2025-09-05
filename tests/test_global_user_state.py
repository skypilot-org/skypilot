import asyncio
import sys
import unittest.mock as mock

import pytest

import sky


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

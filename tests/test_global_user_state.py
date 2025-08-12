import sys

import pytest

import sky
import unittest.mock as mock
import asyncio


@pytest.mark.skipif(sys.platform != 'linux', reason='Only test in CI.')
def test_enabled_clouds_empty():
    # In test environment, no cloud should be enabled.
    assert sky.global_user_state.get_cached_enabled_clouds(
        sky.clouds.cloud.CloudCapability.COMPUTE, workspace='default') == []

# TODO (lloyd-brown): Add test for cluster event retention daemon.
@pytest.mark.asyncio
async def test_cluster_event_retention_daemon():
    """Test the cluster event retention daemon runs correctly."""
    with mock.patch(
            'sky.global_user_state.skypilot_config') as mock_config:
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
                


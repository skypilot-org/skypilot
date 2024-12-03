from unittest import mock

from sky.exceptions import ClusterDoesNotExist
from sky.jobs import recovery_strategy


@mock.patch('sky.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_retry_on_value_error(mock_set_internal,
                                                mock_sky_down) -> None:
    # Set up mock to fail twice with ValueError, then succeed
    mock_sky_down.side_effect = [
        ValueError('Mock error 1'),
        ValueError('Mock error 2'),
        None,
    ]

    # Call should succeed after retries
    recovery_strategy.terminate_cluster('test-cluster')

    # Verify sky.down was called 3 times
    assert mock_sky_down.call_count == 3
    mock_sky_down.assert_has_calls([
        mock.call('test-cluster'),
        mock.call('test-cluster'),
        mock.call('test-cluster'),
    ])

    # Verify usage.set_internal was called before each sky.down
    assert mock_set_internal.call_count == 3


@mock.patch('sky.down')
@mock.patch('sky.usage.usage_lib.messages.usage.set_internal')
def test_terminate_cluster_handles_nonexistent_cluster(mock_set_internal,
                                                       mock_sky_down) -> None:
    # Set up mock to raise ClusterDoesNotExist
    mock_sky_down.side_effect = ClusterDoesNotExist('test-cluster')

    # Call should succeed silently
    recovery_strategy.terminate_cluster('test-cluster')

    # Verify sky.down was called once
    assert mock_sky_down.call_count == 1
    mock_sky_down.assert_called_once_with('test-cluster')

    # Verify usage.set_internal was called once
    assert mock_set_internal.call_count == 1

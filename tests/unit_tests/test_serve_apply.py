"""Unit tests for sky serve apply."""
from unittest import mock

import pytest

from sky import exceptions
from sky.serve.client import impl

# Note: decorators are applied bottom-up, so arguments are injected in reverse order.
# Order: stream_and_get -> mock_stream, status -> mock_status, update -> mock_update, up -> mock_up


@mock.patch('sky.serve.client.impl.up')
@mock.patch('sky.serve.client.impl.update')
@mock.patch('sky.serve.client.impl.status')  # <--- ADDED THIS
@mock.patch('sky.client.sdk.stream_and_get')
def test_apply_creates_service_when_not_found(mock_stream, mock_status,
                                              mock_update, mock_up):
    """
    Scenario: User runs 'apply'. Service does NOT exist.
    Expected: Code calls 'up()' (Create).
    """
    # 1. Simulate "Service Not Found" (stream_and_get returns empty list)
    mock_stream.return_value = []

    # 2. Call the function
    impl.apply(task="dummy_task",
               workers=None,
               service_name="test-service",
               mode="update_mode")

    # 3. Assertions
    mock_up.assert_called_once()  # Should call UP
    mock_update.assert_not_called()  # Should NOT call UPDATE


@mock.patch('sky.serve.client.impl.up')
@mock.patch('sky.serve.client.impl.update')
@mock.patch('sky.serve.client.impl.status')
@mock.patch('sky.client.sdk.stream_and_get')
def test_apply_creates_service_when_cluster_down(mock_stream, mock_status,
                                                 mock_update, mock_up):
    """
    Scenario: User runs 'apply'. Controller is DOWN (ClusterNotUpError).
    Expected: Code catches error and calls 'up()' (Create).
    """
    # 1. Simulate "Controller Down" exception
    mock_stream.side_effect = exceptions.ClusterNotUpError("Controller is down")

    # 2. Call the function
    impl.apply(task="dummy_task",
               workers=None,
               service_name="test-service",
               mode="update_mode")

    # 3. Assertions
    mock_up.assert_called_once()  # Should call UP
    mock_update.assert_not_called()  # Should NOT call UPDATE


@mock.patch('sky.serve.client.impl.up')
@mock.patch('sky.serve.client.impl.update')
@mock.patch('sky.serve.client.impl.status')
@mock.patch('sky.client.sdk.stream_and_get')
def test_apply_updates_service_when_exists(mock_stream, mock_status,
                                           mock_update, mock_up):
    """
    Scenario: User runs 'apply'. Service ALREADY exists.
    Expected: Code calls 'update()' (Update).
    """
    # 1. Simulate "Service Found" (Returns a list with one record)
    mock_stream.return_value = [{'name': 'test-service', 'status': 'READY'}]

    # 2. Call the function
    impl.apply(task="dummy_task",
               workers=None,
               service_name="test-service",
               mode="update_mode")

    # 3. Assertions
    mock_update.assert_called_once()  # Should call UPDATE
    mock_up.assert_not_called()  # Should NOT call UP

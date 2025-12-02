"""Unit tests for sky serve apply."""
from unittest import mock

import pytest

import sky
from sky.serve.client import impl


# Mock dependencies to prevent real network calls
@mock.patch('sky.server.common.check_server_healthy_or_start_fn')
@mock.patch('sky.client.sdk.stream_and_get')
@mock.patch('sky.server.common.make_authenticated_request')
@mock.patch('sky.client.common.upload_mounts_to_api_server')
@mock.patch('sky.client.sdk.optimize')
@mock.patch('sky.client.sdk.validate')
def test_apply_sends_request_correctly(mock_validate, mock_optimize,
                                       mock_upload, mock_request, mock_stream,
                                       mock_server_check):
    """
    Scenario: User runs 'apply'.
    Expected: Client processes the DAG and sends a POST request to '/serve/apply'.
    """
    # 1. SETUP: Create a valid Task object
    task = sky.Task(run="echo hello")
    service_name = "test-service"

    # 2. SETUP: Configure the Mocks
    mock_optimize.return_value = "request-id"
    mock_upload.side_effect = lambda dag: dag

    # --- FIX FOR RUNTIME ERROR ---
    # Create a fake HTTP Response object that looks like a success
    mock_response = mock.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"request_id": "job-123"}
    # Tell the request mock to return this fake response
    mock_request.return_value = mock_response
    # -----------------------------

    # 3. EXECUTE: Call the function
    impl.apply(task=task,
               workers=None,
               service_name=service_name,
               mode="replace")

    # 4. ASSERTIONS

    # Ensure validation and optimization happened
    mock_validate.assert_called_once()
    mock_optimize.assert_called_once()
    mock_upload.assert_called_once()

    # Verify we called the backend endpoint correctly
    assert mock_request.call_count == 1

    args, kwargs = mock_request.call_args
    method = args[0]
    endpoint = args[1]

    assert method == 'POST'
    assert endpoint == '/serve/apply'

    # Verify payload contains service name
    payload = kwargs['json']
    assert payload['service_name'] == service_name
    assert payload['mode'] == 'replace'
    assert 'task' in payload

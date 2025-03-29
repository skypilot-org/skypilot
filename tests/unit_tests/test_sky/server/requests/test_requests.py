"""Unit tests for sky.server.requests.requests module."""
import pytest

from sky.server.requests import payloads
from sky.server.requests import requests
from sky.server.requests.requests import RequestStatus


def dummy():
    return None


def test_set_request_failed():
    request = requests.Request(request_id='test-request-1',
                               name='test-request',
                               entrypoint=dummy,
                               request_body=payloads.RequestBody(),
                               status=RequestStatus.RUNNING,
                               created_at=0.0,
                               user_id='test-user')

    requests.create_if_not_exists(request)
    try:
        raise ValueError('Boom!')
    except ValueError as e:
        requests.set_request_failed('test-request-1', e)

    # Get the updated request
    updated_request = requests.get_request('test-request-1')

    # Verify the request was updated correctly
    assert updated_request is not None
    assert updated_request.status == RequestStatus.FAILED

    # Verify the error was set correctly
    error = updated_request.get_error()
    assert error is not None
    assert error['type'] == 'ValueError'
    assert error['message'] == 'Boom!'
    assert error['object'] is not None


def test_set_request_failed_nonexistent_request():
    # Try to set a non-existent request as failed
    with pytest.raises(AssertionError):
        requests.set_request_failed('nonexistent-request',
                                    ValueError('Test error'))

import inspect
from unittest import mock

import pytest
import requests

import sky
from sky import exceptions
from sky.client import sdk


@pytest.fixture
def validate_response(monkeypatch):
    monkeypatch.setattr(sdk.versions, 'get_remote_api_version', lambda: 999)
    response = requests.Response()
    response.url = 'https://synthetic.invalid/validate'
    response._content = b'{}'
    request = mock.Mock(return_value=response)
    monkeypatch.setattr(sdk.server_common, 'make_authenticated_request',
                        request)
    return response, request


def validate():
    dag = sky.Dag()
    dag.add(sky.Task(run='true'))
    # Exercise the real validator without health checks or usage reporting.
    inspect.unwrap(sdk.validate)(dag)


def test_validate_accepts_success(validate_response):
    response, request = validate_response
    response.status_code = 200
    validate()
    assert request.call_count == 1
    assert request.call_args.args == ('POST', '/validate')


@pytest.mark.parametrize('status', [401, 403, 422, 500, 502, 503])
def test_validate_rejects_http_error(validate_response, status):
    response, _ = validate_response
    response.status_code = status
    with pytest.raises(requests.HTTPError) as caught:
        validate()
    assert caught.value.response is response


@pytest.mark.parametrize('status', [204, 302])
def test_validate_rejects_unexpected_status(validate_response, status):
    response, _ = validate_response
    response.status_code = status
    with pytest.raises(RuntimeError, match='Failed to process response'):
        validate()


def test_validate_preserves_permission_error(validate_response):
    response, _ = validate_response
    response.status_code = 403
    response._content = b'{"detail": "workspace access denied"}'
    with pytest.raises(exceptions.PermissionDeniedError,
                       match='workspace access denied'):
        validate()


def test_validate_preserves_validation_error(validate_response, monkeypatch):
    response, _ = validate_response
    response.status_code = 400
    error = ValueError('Invalid resources.config override')
    monkeypatch.setattr(exceptions, 'deserialize_exception', lambda _: error)
    with pytest.raises(ValueError, match='Invalid resources.config override'):
        validate()

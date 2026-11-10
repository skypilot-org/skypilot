"""Tests for sky.provision.kubernetes.api_errors."""
import json
from unittest import mock

import pytest

from sky.provision.kubernetes import api_errors
from sky.provision.kubernetes import instance
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.provision.kubernetes import volume


class FakeApiException(Exception):

    def __init__(self, status, body=None, headers=None):
        super().__init__(status)
        self.status = status
        self.body = body
        self.headers = headers


class FakeMaxRetryError(Exception):
    pass


class FakeHttpError(Exception):
    pass


@pytest.fixture(autouse=True)
def fake_kubernetes(monkeypatch):
    monkeypatch.setattr('sky.adaptors.kubernetes.api_exception',
                        lambda: FakeApiException)
    monkeypatch.setattr('sky.adaptors.kubernetes.max_retry_error',
                        lambda: FakeMaxRetryError)
    monkeypatch.setattr('sky.adaptors.kubernetes.urllib3_http_error',
                        lambda: FakeHttpError)


def _status(code, reason, message):
    return FakeApiException(code,
                            body=json.dumps({
                                'kind': 'Status',
                                'message': message,
                                'reason': reason,
                                'code': code,
                            }))


@pytest.mark.parametrize('body,expected', [
    (json.dumps({'message': 'pods "x" is forbidden'}), 'pods "x" is forbidden'),
    (b'{"message": "as bytes"}', 'as bytes'),
    ('<html>503 Service Unavailable</html>',
     '<html>503 Service Unavailable</html>'),
    (b'<html>bytes page</html>', '<html>bytes page</html>'),
    (json.dumps(['not', 'a', 'status']), '["not", "a", "status"]'),
    (None, ''),
])
def test_status_message(body, expected):
    assert api_errors.status_message(FakeApiException(500, body)) == expected


def test_is_transport_error():
    assert api_errors.is_transport_error(FakeApiException(0))
    assert not api_errors.is_transport_error(FakeApiException(503))
    assert api_errors.is_transport_error(FakeHttpError())
    assert not api_errors.is_transport_error(ValueError())


@pytest.mark.parametrize('headers,expected', [
    ({
        'CF-RAY': 'abc'
    }, True),
    ({
        'Server': 'cloudflare'
    }, True),
    ({
        'Server': 'nginx'
    }, False),
    ({}, False),
    (None, False),
])
def test_is_cloudflare_403(headers, expected):
    assert api_errors.is_cloudflare_403(FakeApiException(
        403, headers=headers)) is expected
    # Only a 403 can be a CloudFlare 403.
    assert not api_errors.is_cloudflare_403(
        FakeApiException(401, headers={'CF-RAY': 'abc'}))


@pytest.mark.parametrize('exception,expected', [
    (FakeMaxRetryError(), True),
    (FakeApiException(429), True),
    (FakeApiException(500), True),
    (FakeApiException(503), True),
    (FakeApiException(403, headers={'CF-RAY': 'abc'}), True),
    (FakeApiException(403), False),
    (FakeApiException(404), False),
    (FakeApiException(409), False),
    (FakeApiException(422), False),
    (ValueError(), False),
    (None, False),
])
def test_is_transient(exception, expected):
    assert api_errors.is_transient(exception) is expected


@pytest.mark.parametrize('status,expected', [(401, True), (403, True),
                                             (404, False), (500, False)])
def test_is_rbac_denial(status, expected):
    assert api_errors.is_rbac_denial(FakeApiException(status)) is expected


def test_unavailable_webhook():
    message = ('Internal error occurred: failed calling webhook "mpod.kb.io": '
               'no endpoints available for service "kueue-webhook-service"')
    assert api_errors.unavailable_webhook(_status(500, 'InternalError',
                                                  message)) == 'mpod.kb.io'
    # A non-JSON body still names the webhook.
    assert api_errors.unavailable_webhook(FakeApiException(
        500, body=message)) == 'mpod.kb.io'
    assert api_errors.unavailable_webhook(
        _status(500, 'InternalError', 'etcdserver: request timed out')) is None
    # The same text with another status is not an unreachable webhook.
    assert api_errors.unavailable_webhook(_status(400, 'BadRequest',
                                                  message)) is None
    # A missing or non-object body is not an error of its own.
    assert api_errors.unavailable_webhook(FakeApiException(500)) is None
    assert api_errors.unavailable_webhook(
        FakeApiException(500, body=json.dumps(['not', 'a', 'status']))) is None


def test_invalid_pod_spec_message():
    message = ('Pod "x" is invalid: spec.volumes[1].name: Duplicate value: '
               '"dshm"')
    assert api_errors.invalid_pod_spec_message(_status(422, 'Invalid',
                                                       message)) == message
    assert api_errors.invalid_pod_spec_message(
        _status(
            422, 'Invalid', 'admission webhook "v.example.com" denied the '
            'request: spec.priorityClassName: Invalid value')) is None
    assert api_errors.invalid_pod_spec_message(_status(422, '',
                                                       message)) is None
    assert api_errors.invalid_pod_spec_message(
        _status(403, 'Forbidden', 'exceeded quota')) is None
    assert api_errors.invalid_pod_spec_message(
        FakeApiException(422, body='not json')) is None


def test_old_names_still_point_here():
    """Callers in and outside this repo use the old names."""
    assert instance._invalid_pod_spec_message is (
        api_errors.invalid_pod_spec_message)
    assert instance._WEBHOOK_UNAVAILABLE_PATTERN is (
        api_errors.WEBHOOK_UNAVAILABLE_PATTERN)
    assert kubernetes_utils.is_transient_api_error is api_errors.is_transient
    assert kubernetes_utils._is_cloudflare_403_error is (
        api_errors.is_cloudflare_403)
    assert volume._is_rbac_permission_error is api_errors.is_rbac_denial
    with mock.patch.object(api_errors, 'is_transport_error',
                           return_value=True) as patched:
        assert instance._is_transport_error(ValueError())
        patched.assert_called_once()

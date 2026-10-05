"""Unit tests for sky.provision.shadeform.shadeform_utils.make_request.

Focus: a non-2xx response's BODY must survive into the raised HTTPError —
requests' raise_for_status() message carries only the status line, so without
this the provider's own error_code/message never reached any log or exception.
Live evidence (dev-usw2, 2026-09-23): the shadeform 409 on /instances/create
whose body named error_code=INSUFFICIENT_FUNDS while its status line read like
a generic conflict, which downstream tooling misread as a stockout.
"""

import pytest
import requests

from sky.provision.shadeform import shadeform_utils


class _FakeResponse:
    """Just enough of requests.Response for make_request's paths."""

    def __init__(self, *, status_code=200, reason='OK', url='', text=''):
        self.status_code = status_code
        self.reason = reason
        self.url = url
        self.text = text
        self._json = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            word = 'Client Error' if self.status_code < 500 else 'Server Error'
            raise requests.HTTPError(
                f'{self.status_code} {word}: {self.reason} for url: {self.url}',
                response=self)

    def json(self):
        return self._json


class _FakeRequests:
    HTTPError = requests.HTTPError

    def __init__(self, response):
        self._response = response

    def request(self, *args, **kwargs):
        return self._response


def _patch(monkeypatch, response):
    monkeypatch.setattr(shadeform_utils, 'get_api_key', lambda: 'test-key')
    monkeypatch.setattr(shadeform_utils, 'requests', _FakeRequests(response))


def test_error_body_is_preserved_in_the_raised_httperror(monkeypatch):
    body = ('{"error_code":"INSUFFICIENT_FUNDS","error":"Balance must be a '
            'minimum of $5 to launch an instance. Current balance '
            '[$-0.0245169203]."}')
    _patch(
        monkeypatch,
        _FakeResponse(status_code=409,
                      reason='Conflict',
                      url='https://api.shadeform.ai/v1/instances/create',
                      text=body))
    with pytest.raises(requests.HTTPError) as exc_info:
        shadeform_utils.make_request('POST', '/instances/create', json={})
    message = str(exc_info.value)
    # The status-line prefix is kept byte-identical: downstream classifiers
    # match on '409 Client Error: Conflict for url: <endpoint>'.
    assert message.startswith('409 Client Error: Conflict for url: '
                              'https://api.shadeform.ai/v1/instances/create')
    # ...and the provider's own body rides verbatim behind it.
    assert body in message


def test_success_and_bodyless_error_paths_are_unchanged(monkeypatch):
    ok = _FakeResponse(text='{"id": "abc"}')
    ok._json = {'id': 'abc'}
    _patch(monkeypatch, ok)
    assert shadeform_utils.make_request('POST',
                                        '/instances/create') == {'id': 'abc'}

    # A bodyless 5xx must still raise the unmodified status-line error.
    empty_error = _FakeResponse(status_code=500,
                                reason='Internal Server Error',
                                url='https://api.shadeform.ai/v1/instances/'
                                    'create')
    _patch(monkeypatch, empty_error)
    with pytest.raises(requests.HTTPError) as exc_info:
        shadeform_utils.make_request('POST', '/instances/create')
    assert str(exc_info.value).startswith(
        '500 Server Error: Internal Server Error for url: '
        'https://api.shadeform.ai/v1/instances/create')

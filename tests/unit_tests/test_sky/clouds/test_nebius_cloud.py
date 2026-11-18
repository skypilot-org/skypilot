"""Tests for the Nebius cloud's compute credential check."""

import json
import logging
import types
from unittest import mock

import pytest

from sky.adaptors import nebius as nebius_adaptor
from sky.clouds import nebius as nebius_cloud


class _RequestError(Exception):
    """Stand-in for nebius.aio.service_error.RequestError."""

    def __init__(self, code: str, message: str, request_id: str = 'req-1'):
        super().__init__(message)
        self.status = types.SimpleNamespace(
            code=types.SimpleNamespace(name=code),
            message=message,
            request_id=request_id,
            trace_id='trace-1')


@pytest.fixture
def iam(monkeypatch):
    """Nebius adaptor with credentials present and a mocked IAM client."""
    monkeypatch.setattr(nebius_adaptor, 'is_token_or_cred_file_exist',
                        lambda: True)
    monkeypatch.setattr(nebius_adaptor, 'get_tenant_id',
                        lambda: 'tenant-e00test')
    monkeypatch.setattr(nebius_adaptor, 'sdk', mock.Mock)
    monkeypatch.setattr(nebius_adaptor, 'request_error', lambda: _RequestError)
    monkeypatch.setattr(nebius_adaptor, 'fail_fast_auth_options',
                        lambda: {'fail': 'fast'})
    iam_module = mock.Mock()
    monkeypatch.setattr(nebius_adaptor, 'iam', lambda: iam_module)
    return iam_module


def _fail_list(iam, error: Exception) -> None:
    wait = iam.ProjectServiceClient.return_value.list.return_value.wait
    wait.side_effect = error


def test_compute_check_bounds_and_surfaces_auth(iam):
    ok, reason = nebius_cloud.Nebius._check_compute_credentials()
    assert ok and reason is None
    _, kwargs = iam.ProjectServiceClient.return_value.list.call_args
    assert kwargs == {
        'auth_timeout': nebius_adaptor.CREDENTIAL_CHECK_TIMEOUT,
        'auth_options': {
            'fail': 'fast'
        },
    }


def test_compute_check_reports_a_rejected_key_concisely(iam):
    _fail_list(
        iam,
        _RequestError(
            'INVALID_ARGUMENT',
            'Public Key not exists, expired or deactivated: \'publickey-x\''))
    ok, reason = nebius_cloud.Nebius._check_compute_credentials()
    assert not ok
    first, *hint = reason.splitlines()
    assert first == ('INVALID_ARGUMENT: Public Key not exists, expired or '
                     'deactivated: \'publickey-x\' (request ID: req-1)')
    assert 'trace' not in reason
    assert any('rejected the credentials' in line for line in hint)


@pytest.mark.parametrize('code,expected', [
    ('PERMISSION_DENIED', 'needs the editor role in tenant tenant-e00test'),
    ('NOT_FOUND', 'Check the tenant ID'),
])
def test_compute_check_hint_matches_the_error(iam, code, expected):
    _fail_list(iam, _RequestError(code, 'denied'))
    ok, reason = nebius_cloud.Nebius._check_compute_credentials()
    assert not ok
    assert expected in reason


@pytest.mark.parametrize('error,expected', [
    (json.JSONDecodeError('Expecting value', 'x', 0), 'not valid JSON'),
    (KeyError('kid'), "missing field 'kid'"),
    (ValueError('Unable to load PEM file.'), 'Unable to load PEM file.'),
])
def test_compute_check_reports_a_malformed_credentials_file(
        iam, monkeypatch, error, expected):
    del iam

    def broken_sdk():
        raise error

    monkeypatch.setattr(nebius_adaptor, 'sdk', broken_sdk)
    ok, reason = nebius_cloud.Nebius._check_compute_credentials()
    assert not ok
    assert reason.startswith('Failed to load Nebius credentials from ')
    assert expected in reason.splitlines()[0]
    assert 'Traceback' not in reason


def test_fail_fast_auth_options_use_sdk_option_keys():
    options = pytest.importorskip('nebius.aio.token.options')
    assert nebius_adaptor.fail_fast_auth_options() == {
        options.OPTION_RENEW_SYNCHRONOUS: '1',
        options.OPTION_REPORT_ERROR: '1',
    }


def _record(msg: str, pathname: str = '/app/sky/clouds/nebius.py'):
    return logging.LogRecord('x', logging.WARNING, pathname, 1, msg, None, None)


def test_deprecation_filter_drops_nebius_service_warnings_from_any_frame():
    keep = nebius_adaptor._NebiusDeprecationFilter().filter  # pylint: disable=protected-access
    assert not keep(
        _record('Service nebius.iam.v1.ProjectService is deprecated.'))
    assert not keep(_record('anything', '/site-packages/nebius/api/x.py'))
    assert keep(_record('Service foo.Bar is deprecated.'))


def test_token_refresh_filter_moves_errors_to_debug():
    keep = nebius_adaptor._NebiusTokenRefreshFilter().filter  # pylint: disable=protected-access
    with mock.patch.object(nebius_adaptor.logger, 'debug') as debug:
        assert not keep(_record('Failed refresh token, attempt: 1'))
    debug.assert_called_once()

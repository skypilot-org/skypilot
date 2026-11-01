"""Tests for the Nebius cloud's compute credential check."""

from unittest import mock

import pytest

from sky.adaptors import nebius as nebius_adaptor
from sky.clouds import nebius as nebius_cloud


class _RequestError(Exception):

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


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


def test_compute_check_reports_the_request_error(iam):
    wait = iam.ProjectServiceClient.return_value.list.return_value.wait
    wait.side_effect = _RequestError(
        'INVALID_ARGUMENT: Public Key not exists, expired or deactivated')
    ok, reason = nebius_cloud.Nebius._check_compute_credentials()
    assert not ok
    assert reason.startswith('INVALID_ARGUMENT: Public Key not exists')


def test_fail_fast_auth_options_use_sdk_option_keys():
    options = pytest.importorskip('nebius.aio.token.options')
    assert nebius_adaptor.fail_fast_auth_options() == {
        options.OPTION_RENEW_SYNCHRONOUS: '1',
        options.OPTION_REPORT_ERROR: '1',
    }

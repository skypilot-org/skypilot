"""Tests for the OCI cloud."""
from unittest import mock

import jsonschema
import pytest

from sky.adaptors import oci as oci_adaptor
from sky.adaptors import oci_s3
from sky.clouds import oci as oci_cloud
from sky.clouds.utils import oci_utils
from sky.utils import schemas

# ---------------------------------------------------------------------------
# Credential checks for session-token (`oci session authenticate`) profiles
# ---------------------------------------------------------------------------

_TENANCY = 'ocid1.tenancy.oc1..aaaaaaaatest'
_USER = 'ocid1.user.oc1..aaaaaaaatest'


def _api_key_config():
    return {
        'user': _USER,
        'fingerprint': 'aa:bb',
        'tenancy': _TENANCY,
        'region': 'us-phoenix-1',
        'key_file': '~/.oci/oci_api_key.pem',
    }


def _session_token_config():
    return {
        'fingerprint': 'aa:bb',
        'tenancy': _TENANCY,
        'region': 'us-phoenix-1',
        'key_file': '~/.oci/sessions/TOKEN/oci_api_key.pem',
        'security_token_file': '~/.oci/sessions/TOKEN/token',
    }


@pytest.fixture(name='oci_credentials')
def fixture_oci_credentials(tmp_path, monkeypatch):
    """Points the OCI cloud at a config file that exists and stubs the SDK.

    Returns a function that installs a given profile config + identity
    client and yields the mocked identity client.
    """
    config_file = tmp_path / 'config'
    config_file.write_text('[TOKEN]\n')
    monkeypatch.setattr(oci_adaptor, 'get_config_file',
                        lambda: str(config_file))
    monkeypatch.setattr(oci_utils.oci_config, 'get_profile', lambda: 'TOKEN')

    def install(config, client):
        monkeypatch.setattr(oci_adaptor,
                            'get_oci_config',
                            lambda region=None, profile='DEFAULT': config)
        monkeypatch.setattr(oci_adaptor,
                            'get_identity_client',
                            lambda region=None, profile='DEFAULT': client)
        return client

    return install


def test_check_credentials_api_key_profile_looks_up_user(oci_credentials):
    pytest.importorskip('oci')
    client = oci_credentials(_api_key_config(), mock.MagicMock())
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert ok, msg
    client.get_user.assert_called_once_with(_USER)
    client.list_availability_domains.assert_not_called()


def test_check_credentials_session_token_profile_probes_tenancy(
        oci_credentials):
    pytest.importorskip('oci')
    client = oci_credentials(_session_token_config(), mock.MagicMock())
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert ok, msg
    # There is no `user` in a session-token profile to look up.
    client.get_user.assert_not_called()
    client.list_availability_domains.assert_called_once_with(
        compartment_id=_TENANCY)


def test_check_credentials_explains_401_for_session_token(oci_credentials):
    oci_sdk = pytest.importorskip('oci')
    client = mock.MagicMock()
    client.list_availability_domains.side_effect = (
        oci_sdk.exceptions.ServiceError(status=401,
                                        code='NotAuthenticated',
                                        headers={},
                                        message='The required information to '
                                        'complete authentication was not '
                                        'provided or was incorrect.'))
    oci_credentials(_session_token_config(), client)
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert not ok
    assert 'HTTP 401' in msg
    assert 'oci session authenticate --profile-name TOKEN' in msg
    assert 'NotAuthenticated' in msg


def test_check_credentials_401_for_api_key_is_unchanged(oci_credentials):
    oci_sdk = pytest.importorskip('oci')
    client = mock.MagicMock()
    client.get_user.side_effect = oci_sdk.exceptions.ServiceError(
        status=401, code='NotAuthenticated', headers={}, message='nope')
    oci_credentials(_api_key_config(), client)
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert not ok
    assert 'OCI credential is not correctly set' in msg
    assert 'oci session authenticate' not in msg.split('Error details')[1]


def test_check_credentials_reports_missing_or_expired_token(oci_credentials):
    error = oci_adaptor.OCISessionTokenError(
        'The OCI session token for profile \'TOKEN\' has expired. Run `oci '
        'session authenticate --profile-name TOKEN` to get a new token.')
    client = mock.MagicMock()
    oci_credentials(_session_token_config(), client)
    with mock.patch.object(oci_adaptor,
                           'get_identity_client',
                           side_effect=error):
        # pylint: disable=protected-access
        ok, msg = oci_cloud.OCI._check_credentials()
    assert not ok
    assert msg.startswith(str(error))
    client.list_availability_domains.assert_not_called()


def test_credential_file_mounts_include_session_token(monkeypatch):
    monkeypatch.setattr(oci_s3, 'use_s3_api', lambda: False)
    monkeypatch.setattr(oci_adaptor, 'get_config_file', lambda: '~/.oci/config')
    monkeypatch.setattr(
        oci_adaptor,
        'get_oci_config',
        lambda region=None, profile='DEFAULT': _session_token_config())
    mounts = oci_cloud.OCI().get_credential_file_mounts()
    for path in ('~/.oci/config', '~/.oci/sessions/TOKEN/oci_api_key.pem',
                 '~/.oci/sessions/TOKEN/token'):
        assert mounts[path] == path


def test_credential_file_mounts_for_api_key_profile_unchanged(monkeypatch):
    monkeypatch.setattr(oci_s3, 'use_s3_api', lambda: False)
    monkeypatch.setattr(oci_adaptor, 'get_config_file', lambda: '~/.oci/config')
    monkeypatch.setattr(
        oci_adaptor,
        'get_oci_config',
        lambda region=None, profile='DEFAULT': _api_key_config())
    mounts = oci_cloud.OCI().get_credential_file_mounts()
    assert '~/.oci/config' in mounts
    assert '~/.oci/oci_api_key.pem' in mounts
    assert not any('token' in path for path in mounts)


def test_config_schema_accepts_oci_config_profile_per_region():
    # `oci_config_profile` is documented under `region_configs.default`, so
    # the schema must not reject it there.
    jsonschema.validate(
        {
            'oci': {
                'region_configs': {
                    'default': {
                        'oci_config_profile': 'TOKEN',
                        'compartment_ocid': 'ocid1.compartment.oc1..aaaa',
                    }
                }
            }
        }, schemas.get_config_schema())

"""Tests for OCI adaptor."""
import base64
import json
import logging
import pathlib
import time
from unittest import mock

import pytest

from sky import check as sky_check
from sky.adaptors import oci
from sky.utils import log_utils


def test_oci_circuit_breaker_logging():
    """Test that OCI circuit breaker logging is properly configured."""
    # Get the circuit breaker logger
    logger = logging.getLogger('oci.circuit_breaker')

    # Create a handler that captures log records
    log_records = []
    test_handler = logging.Handler()
    test_handler.emit = lambda record: log_records.append(record)
    logger.addHandler(test_handler)

    # Create a null handler to suppress logs during import
    null_handler = logging.NullHandler()
    logger.addHandler(null_handler)

    try:
        # Verify logger starts at WARNING level (set by adaptor initialization)
        initial_level = logger.getEffectiveLevel()
        print(
            f'Initial logger level: {initial_level} (WARNING={logging.WARNING})'
        )
        assert initial_level == logging.WARNING, (
            'OCI circuit breaker logger should be set to WARNING before initialization'
        )

        # Force OCI module import through LazyImport by accessing a module attribute
        print('Attempting to import OCI module...')
        try:
            # This will trigger LazyImport's load_module for the actual OCI module
            _ = oci.oci.config.DEFAULT_LOCATION
        except (ImportError, AttributeError) as e:
            # Expected when OCI SDK is not installed
            print(f'Import/Attribute error as expected: {e}')
            pass

        # Verify logger level after import attempt
        after_level = logger.getEffectiveLevel()
        print(
            f'Logger level after import: {after_level} (WARNING={logging.WARNING})'
        )
        assert after_level == logging.WARNING, (
            'OCI circuit breaker logger should remain at WARNING after initialization'
        )

        # Verify no circuit breaker logs were emitted
        circuit_breaker_logs = [
            record for record in log_records
            if 'Circuit breaker' in record.getMessage()
        ]
        assert not circuit_breaker_logs, (
            'No circuit breaker logs should be emitted during initialization')
    finally:
        # Clean up the handlers
        logger.removeHandler(test_handler)
        logger.removeHandler(null_handler)


# ---------------------------------------------------------------------------
# Session-token (`oci session authenticate`) profiles
# ---------------------------------------------------------------------------

_TENANCY = 'ocid1.tenancy.oc1..aaaaaaaatest'
_USER = 'ocid1.user.oc1..aaaaaaaatest'
_FINGERPRINT = 'aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99'


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _make_jwt(exp: float) -> str:
    """An unsigned JWT with the claims a session token carries."""
    header = _b64url(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())
    payload = _b64url(
        json.dumps({
            'sub': _USER,
            'tenant': _TENANCY,
            'iat': int(exp) - 3600,
            'exp': int(exp),
        }).encode())
    return f'{header}.{payload}.'


def _write_private_key(path: pathlib.Path) -> None:
    # pylint: disable=import-outside-toplevel
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))


@pytest.fixture(name='oci_config_file')
def fixture_oci_config_file(tmp_path: pathlib.Path, monkeypatch):
    """A ~/.oci/config with an API-key and a session-token profile.

    Returns the path of the session token file so tests can tamper with it.
    """
    pytest.importorskip('oci')
    key_file = tmp_path / 'oci_api_key.pem'
    _write_private_key(key_file)
    token_file = tmp_path / 'token'
    token_file.write_text(_make_jwt(time.time() + 3600))
    (tmp_path / 'config').write_text('[APIKEY]\n'
                                     f'user={_USER}\n'
                                     f'fingerprint={_FINGERPRINT}\n'
                                     f'tenancy={_TENANCY}\n'
                                     'region=us-phoenix-1\n'
                                     f'key_file={key_file}\n'
                                     '\n'
                                     '[TOKEN]\n'
                                     f'fingerprint={_FINGERPRINT}\n'
                                     f'tenancy={_TENANCY}\n'
                                     'region=us-phoenix-1\n'
                                     f'key_file={key_file}\n'
                                     f'security_token_file={token_file}\n')
    monkeypatch.setenv(oci.ENV_VAR_OCI_CONFIG, str(tmp_path / 'config'))
    return token_file


def _client_call_args(profile: str):
    """Constructs a compute client for `profile`, returning (config, kwargs)."""
    with mock.patch.object(oci.oci.core, 'ComputeClient') as client_cls:
        oci.get_core_client(region='us-ashburn-1', profile=profile)
    assert client_cls.call_count == 1
    (config,), kwargs = client_cls.call_args
    return config, kwargs


def test_api_key_profile_passes_plain_config(oci_config_file):
    del oci_config_file  # Only the config file itself is needed.
    config, kwargs = _client_call_args('APIKEY')
    assert config['user'] == _USER
    assert config['region'] == 'us-ashburn-1'
    assert not oci.is_session_token_config(config)
    # API-key profiles are untouched: no signer, the SDK signs from the
    # config as before.
    assert kwargs == {}
    assert oci.get_oci_signer(config, 'APIKEY') is None


def test_session_token_profile_builds_security_token_signer(oci_config_file):
    token = oci_config_file.read_text()
    config, kwargs = _client_call_args('TOKEN')
    assert 'user' not in config
    assert oci.is_session_token_config(config)
    signer = kwargs['signer']
    assert isinstance(signer, oci.oci.auth.signers.SecurityTokenSigner)
    # The SDK prefixes session tokens with `ST$` in the keyId; this is what
    # the OCI CLI sends for `--auth security_token`.
    assert signer.api_key == f'ST${token}'


def test_session_token_profile_is_accepted_by_real_sdk_client(oci_config_file):
    del oci_config_file
    # Without the signer, the SDK rejects the profile ({'user': 'missing'});
    # with it, validation is skipped and the client constructs cleanly.
    config = oci.get_oci_config(profile='TOKEN')
    with pytest.raises(oci.oci.exceptions.InvalidConfig):
        oci.oci.identity.IdentityClient(config)
    client = oci.get_identity_client(profile='TOKEN')
    assert isinstance(client, oci.oci.identity.IdentityClient)
    assert client.base_client.signer.api_key.startswith('ST$')


@pytest.mark.parametrize('client_factory', [
    oci.get_core_client,
    oci.get_net_client,
    oci.get_search_client,
    oci.get_identity_client,
    oci.get_object_storage_client,
])
def test_every_client_factory_uses_the_session_token(oci_config_file,
                                                     client_factory):
    del oci_config_file
    client = client_factory(profile='TOKEN')
    assert isinstance(client.base_client.signer,
                      oci.oci.auth.signers.SecurityTokenSigner)


def test_missing_token_file_gives_clear_error(oci_config_file):
    oci_config_file.unlink()
    with pytest.raises(oci.OCISessionTokenError) as exc_info:
        oci.get_identity_client(profile='TOKEN')
    message = str(exc_info.value)
    assert 'does not exist' in message
    assert 'oci session authenticate --profile-name TOKEN' in message


def test_expired_token_gives_clear_error(oci_config_file):
    oci_config_file.write_text(_make_jwt(time.time() - 120))
    with pytest.raises(oci.OCISessionTokenError) as exc_info:
        oci.get_core_client(profile='TOKEN')
    message = str(exc_info.value)
    assert 'has expired' in message
    assert 'oci session authenticate --profile-name TOKEN' in message


def test_unparseable_token_is_left_to_the_service(oci_config_file):
    # Not a JWT: nothing to check locally, so the signer is still built and
    # the service decides (a 401 is then mapped by `sky check`).
    oci_config_file.write_text('not-a-jwt')
    _, kwargs = _client_call_args('TOKEN')
    assert kwargs['signer'].api_key == 'ST$not-a-jwt'


def test_default_profile_resolves_through_skypilot_config(oci_config_file):
    del oci_config_file
    with mock.patch.object(oci.oci_utils.oci_config,
                           'get_profile',
                           return_value='TOKEN'):
        config = oci.get_oci_config()
    assert oci.is_session_token_config(config)


def test_default_profile_is_resolved_for_the_region(oci_config_file):
    # `region_configs.<region>.oci_config_profile` selects the profile for
    # clients built for that region; other regions keep the default one.
    del oci_config_file

    def _profile_for(region=None):
        return 'TOKEN' if region == 'us-ashburn-1' else 'APIKEY'

    with mock.patch.object(oci.oci_utils.oci_config, 'get_profile',
                           _profile_for):
        assert oci.is_session_token_config(
            oci.get_oci_config(region='us-ashburn-1'))
        assert not oci.is_session_token_config(
            oci.get_oci_config(region='us-phoenix-1'))
        assert not oci.is_session_token_config(oci.get_oci_config())
        with mock.patch.object(oci.oci.core, 'ComputeClient') as client_cls:
            oci.get_core_client(region='us-ashburn-1')
            oci.get_core_client(region='us-phoenix-1')
    (ashburn_config,), ashburn_kwargs = client_cls.call_args_list[0]
    (phoenix_config,), phoenix_kwargs = client_cls.call_args_list[1]
    assert isinstance(ashburn_kwargs['signer'],
                      oci.oci.auth.signers.SecurityTokenSigner)
    assert ashburn_config['region'] == 'us-ashburn-1'
    assert phoenix_kwargs == {}
    assert phoenix_config['user'] == _USER


def test_session_token_error_names_the_regional_profile(oci_config_file):
    oci_config_file.unlink()
    with mock.patch.object(oci.oci_utils.oci_config,
                           'get_profile',
                           lambda region=None: 'TOKEN'
                           if region == 'us-ashburn-1' else 'APIKEY'):
        with pytest.raises(oci.OCISessionTokenError) as exc_info:
            oci.get_identity_client(region='us-ashburn-1')
        # The other region uses the API-key profile and is unaffected.
        oci.get_identity_client(region='us-phoenix-1')
    assert 'oci session authenticate --profile-name TOKEN' in str(
        exc_info.value)

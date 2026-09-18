"""Tests for the OCI cloud."""
from concurrent import futures
import functools
import threading
from unittest import mock

import jsonschema
import pytest

from sky.adaptors import oci as oci_adaptor
from sky.adaptors import oci_s3
from sky.clouds import oci as oci_cloud
from sky.clouds.utils import oci_utils
from sky.utils import config_utils
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


def _use_region_configs(monkeypatch, region_configs):
    """Serves `oci.region_configs` from a dict instead of ~/.sky/config.yaml."""
    config = config_utils.Config({'oci': {'region_configs': region_configs}})
    monkeypatch.setattr(
        oci_utils.skypilot_config, 'get_effective_region_config',
        functools.partial(config_utils.get_cloud_config_value_from_dict,
                          config))
    monkeypatch.setattr(
        oci_utils.skypilot_config,
        'get_nested',
        lambda keys, default_value, override_configs=None: config.get_nested(
            keys, default_value, override_configs))


@pytest.fixture(name='oci_credentials')
def fixture_oci_credentials(tmp_path, monkeypatch):
    """Points the OCI cloud at a config file that exists and stubs the SDK.

    The default profile is `TOKEN`. Returns a function that installs that
    profile's loaded config + identity client, plus `(config, client)` pairs
    for further profiles passed by name, and yields the default profile's
    identity client.
    """
    config_file = tmp_path / 'config'
    config_file.write_text('[TOKEN]\n')
    monkeypatch.setattr(oci_adaptor, 'get_config_file',
                        lambda: str(config_file))
    _use_region_configs(monkeypatch,
                        {'default': {
                            'oci_config_profile': 'TOKEN'
                        }})

    def install(config, client, **others):
        by_profile = {'TOKEN': (config, client), **others}
        monkeypatch.setattr(
            oci_adaptor,
            'get_oci_config',
            lambda region=None, profile='DEFAULT': by_profile[profile][0])
        monkeypatch.setattr(
            oci_adaptor,
            'get_identity_client',
            lambda region=None, profile='DEFAULT': by_profile[profile][1])
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


_REGIONAL_PROFILES = {
    'default': {
        'oci_config_profile': 'TOKEN'
    },
    'us-ashburn-1': {
        'oci_config_profile': 'ASHBURN'
    },
    # Uses the default profile; nothing extra to probe.
    'us-phoenix-1': {
        'compartment_ocid': 'ocid1.compartment.oc1..aaaa'
    },
}


def test_check_credentials_probes_every_regional_profile(
        oci_credentials, monkeypatch):
    pytest.importorskip('oci')
    ashburn = mock.MagicMock()
    client = oci_credentials(_session_token_config(),
                             mock.MagicMock(),
                             ASHBURN=(_api_key_config(), ashburn))
    _use_region_configs(monkeypatch, _REGIONAL_PROFILES)
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert ok, msg
    # Each profile is probed once, the way its auth type calls for.
    client.list_availability_domains.assert_called_once_with(
        compartment_id=_TENANCY)
    client.get_user.assert_not_called()
    ashburn.get_user.assert_called_once_with(_USER)


def test_check_credentials_names_the_failing_regional_profile(
        oci_credentials, monkeypatch):
    oci_sdk = pytest.importorskip('oci')
    ashburn = mock.MagicMock()
    ashburn.get_user.side_effect = oci_sdk.exceptions.ServiceError(
        status=401, code='NotAuthenticated', headers={}, message='nope')
    client = oci_credentials(_session_token_config(),
                             mock.MagicMock(),
                             ASHBURN=(_api_key_config(), ashburn))
    _use_region_configs(monkeypatch, _REGIONAL_PROFILES)
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert not ok
    assert msg.startswith(
        "OCI profile 'ASHBURN' (the profile for region 'us-ashburn-1' in ")
    assert 'OCI credential is not correctly set' in msg
    assert 'NotAuthenticated' in msg
    # The default profile was fine and was probed first.
    client.list_availability_domains.assert_called_once()


def test_check_credentials_names_the_failing_default_profile(
        oci_credentials, monkeypatch):
    oci_sdk = pytest.importorskip('oci')
    client = mock.MagicMock()
    client.list_availability_domains.side_effect = (
        oci_sdk.exceptions.ServiceError(status=401,
                                        code='NotAuthenticated',
                                        headers={},
                                        message='expired'))
    ashburn = mock.MagicMock()
    oci_credentials(_session_token_config(),
                    client,
                    ASHBURN=(_api_key_config(), ashburn))
    _use_region_configs(monkeypatch, _REGIONAL_PROFILES)
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert not ok
    assert msg.startswith("OCI profile 'TOKEN' (the default profile in ")
    assert 'oci session authenticate --profile-name TOKEN' in msg
    ashburn.get_user.assert_not_called()


def test_check_credentials_reports_a_regional_profile_missing_from_config(
        oci_credentials, monkeypatch):
    oci_sdk = pytest.importorskip('oci')
    client = oci_credentials(_session_token_config(), mock.MagicMock())
    _use_region_configs(monkeypatch, _REGIONAL_PROFILES)

    def _get_oci_config(region=None, profile='DEFAULT'):
        if profile == 'ASHBURN':
            raise oci_sdk.exceptions.ProfileNotFound(
                'Profile ASHBURN not found in config file')
        return _session_token_config()

    monkeypatch.setattr(oci_adaptor, 'get_oci_config', _get_oci_config)
    # pylint: disable=protected-access
    ok, msg = oci_cloud.OCI._check_credentials()
    assert not ok
    assert msg.startswith("OCI profile 'ASHBURN' (")
    assert 'ProfileNotFound' in msg
    client.list_availability_domains.assert_called_once()


@pytest.fixture(name='credential_mounts')
def fixture_credential_mounts(tmp_path, monkeypatch):
    """Serves `~/.oci/config` profiles from dicts for the credential mounts.

    Returns `install(region_configs, **profiles)`, where `region_configs` is
    the `oci.region_configs` section and each keyword maps a profile name
    to its loaded config; profiles not passed are missing from the config
    file. `install` returns the path of the (empty) OCI config file.
    """
    monkeypatch.setattr(oci_s3, 'use_s3_api', lambda: False)
    config_file = tmp_path / 'config'
    config_file.write_text('')
    monkeypatch.setattr(oci_adaptor, 'get_config_file',
                        lambda: str(config_file))
    # No ~/.sky/config.yaml to copy along.
    monkeypatch.setattr(oci_utils.oci_config, 'get_sky_user_config_file',
                        lambda: str(tmp_path / 'no-config.yaml'))

    def install(region_configs, **profiles):
        _use_region_configs(monkeypatch, region_configs)

        def _get_oci_config(region=None, profile='DEFAULT'):
            if profile not in profiles:
                raise oci_adaptor.oci.exceptions.ProfileNotFound(
                    f'Profile {profile} not found in config file')
            return profiles[profile]

        monkeypatch.setattr(oci_adaptor, 'get_oci_config', _get_oci_config)
        return str(config_file)

    return install


def _profile_config(tmp_path, name, session_token, create=True):
    """A loaded profile whose key (and token) files live under tmp_path/name."""
    files = {'key_file': tmp_path / name / 'oci_api_key.pem'}
    if session_token:
        files[oci_adaptor.SECURITY_TOKEN_FILE_KEY] = tmp_path / name / 'token'
    config = {'tenancy': _TENANCY, 'region': 'us-phoenix-1'}
    for key, path in files.items():
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('')
        config[key] = str(path)
    return config


def _mounted(*paths):
    return {path: path for path in paths}


def test_credential_file_mounts_include_session_token(tmp_path,
                                                      credential_mounts):
    token = _profile_config(tmp_path, 'TOKEN', session_token=True)
    config_file = credential_mounts(
        {'default': {
            'oci_config_profile': 'TOKEN'
        }}, TOKEN=token)
    mounts = oci_cloud.OCI().get_credential_file_mounts()
    assert mounts == _mounted(config_file, token['key_file'],
                              token['security_token_file'])


def test_credential_file_mounts_for_api_key_profile_unchanged(
        tmp_path, credential_mounts):
    api_key = _profile_config(tmp_path, 'DEFAULT', session_token=False)
    config_file = credential_mounts({}, DEFAULT=api_key)
    mounts = oci_cloud.OCI().get_credential_file_mounts()
    assert mounts == _mounted(config_file, api_key['key_file'])


def test_credential_file_mounts_cover_every_regional_profile(
        tmp_path, credential_mounts):
    token = _profile_config(tmp_path, 'TOKEN', session_token=True)
    ashburn = _profile_config(tmp_path, 'ASHBURN', session_token=True)
    config_file = credential_mounts(_REGIONAL_PROFILES,
                                    TOKEN=token,
                                    ASHBURN=ashburn)
    mounts = oci_cloud.OCI().get_credential_file_mounts()
    # Provisioning from the cluster resolves the region's profile the same
    # way, so its key and token must be there too, at the same paths the
    # copied ~/.oci/config points at.
    assert mounts == _mounted(config_file, token['key_file'],
                              token['security_token_file'], ashburn['key_file'],
                              ashburn['security_token_file'])


def test_credential_file_mounts_list_a_shared_file_once(tmp_path,
                                                        credential_mounts):
    token = _profile_config(tmp_path, 'TOKEN', session_token=True)
    # An API-key profile reusing the default profile's key file.
    ashburn = {
        'user': _USER,
        'tenancy': _TENANCY,
        'key_file': token['key_file'],
    }
    config_file = credential_mounts(_REGIONAL_PROFILES,
                                    TOKEN=token,
                                    ASHBURN=ashburn)
    mounts = oci_cloud.OCI().get_credential_file_mounts()
    assert mounts == _mounted(config_file, token['key_file'],
                              token['security_token_file'])


def test_credential_file_mounts_skip_missing_regional_files(
        tmp_path, credential_mounts):
    token = _profile_config(tmp_path, 'TOKEN', session_token=True)
    ashburn = _profile_config(tmp_path,
                              'ASHBURN',
                              session_token=True,
                              create=False)
    config_file = credential_mounts(_REGIONAL_PROFILES,
                                    TOKEN=token,
                                    ASHBURN=ashburn)
    with mock.patch.object(oci_cloud, 'logger') as logger:
        mounts = oci_cloud.OCI().get_credential_file_mounts()
    assert mounts == _mounted(config_file, token['key_file'],
                              token['security_token_file'])
    warnings = [call.args[0] for call in logger.warning.call_args_list]
    assert [ashburn['key_file'] in w for w in warnings] == [True, False]
    assert [ashburn['security_token_file'] in w for w in warnings
           ] == [False, True]
    assert all("'ASHBURN'" in w and 'does not exist' in w for w in warnings)


def test_credential_file_mounts_skip_a_profile_missing_from_config(
        tmp_path, credential_mounts):
    token = _profile_config(tmp_path, 'TOKEN', session_token=True)
    # `ASHBURN` is named in ~/.sky/config.yaml but not in ~/.oci/config.
    config_file = credential_mounts(_REGIONAL_PROFILES, TOKEN=token)
    with mock.patch.object(oci_cloud, 'logger') as logger:
        mounts = oci_cloud.OCI().get_credential_file_mounts()
    assert mounts == _mounted(config_file, token['key_file'],
                              token['security_token_file'])
    warning = logger.warning.call_args.args[0]
    assert warning.startswith(f"OCI profile 'ASHBURN' is not defined in "
                              f'{config_file}')


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


# ---------------------------------------------------------------------------
# Profile selection
# ---------------------------------------------------------------------------


def test_profile_is_default_without_config(monkeypatch):
    _use_region_configs(monkeypatch, {})
    assert oci_utils.oci_config.get_profile() == 'DEFAULT'
    assert oci_utils.oci_config.get_profile('us-phoenix-1') == 'DEFAULT'


def test_default_profile_applies_to_every_region(monkeypatch):
    _use_region_configs(monkeypatch,
                        {'default': {
                            'oci_config_profile': 'TOKEN'
                        }})
    assert oci_utils.oci_config.get_profile() == 'TOKEN'
    assert oci_utils.oci_config.get_profile('us-phoenix-1') == 'TOKEN'


def test_region_names_its_own_profile(monkeypatch):
    _use_region_configs(
        monkeypatch, {
            'default': {
                'oci_config_profile': 'TOKEN'
            },
            'us-ashburn-1': {
                'oci_config_profile': 'ASHBURN',
                'compartment_ocid': 'ocid1.compartment.oc1..aaaa',
            },
        })
    # Named region -> region_configs.default -> DEFAULT, like the compartment.
    assert oci_utils.oci_config.get_profile('us-ashburn-1') == 'ASHBURN'
    assert oci_utils.oci_config.get_profile('us-phoenix-1') == 'TOKEN'
    assert oci_utils.oci_config.get_profile() == 'TOKEN'


def test_region_profile_without_default_falls_back_to_default_profile(
        monkeypatch):
    _use_region_configs(monkeypatch,
                        {'us-ashburn-1': {
                            'oci_config_profile': 'ASHBURN'
                        }})
    assert oci_utils.oci_config.get_profile('us-ashburn-1') == 'ASHBURN'
    assert oci_utils.oci_config.get_profile('us-phoenix-1') == 'DEFAULT'
    assert oci_utils.oci_config.get_profile() == 'DEFAULT'


def test_profiles_in_use_is_just_the_default_without_regional_profiles(
        monkeypatch):
    _use_region_configs(monkeypatch, {})
    assert oci_utils.oci_config.get_profiles_in_use() == [(None, 'DEFAULT')]
    _use_region_configs(
        monkeypatch, {
            'default': {
                'oci_config_profile': 'TOKEN'
            },
            'us-phoenix-1': {
                'compartment_ocid': 'ocid1.compartment.oc1..aaaa'
            },
        })
    assert oci_utils.oci_config.get_profiles_in_use() == [(None, 'TOKEN')]


def test_profiles_in_use_lists_each_regional_profile_once(monkeypatch):
    _use_region_configs(
        monkeypatch,
        {
            'default': {
                'oci_config_profile': 'TOKEN'
            },
            'us-ashburn-1': {
                'oci_config_profile': 'ASHBURN'
            },
            # Same as the default: already covered.
            'us-phoenix-1': {
                'oci_config_profile': 'TOKEN'
            },
            # Same as us-ashburn-1: listed once.
            'us-chicago-1': {
                'oci_config_profile': 'ASHBURN'
            },
            # The literal `DEFAULT` profile differs from the configured
            # default and is kept with its region, which the adaptor needs
            # to resolve it.
            'eu-frankfurt-1': {
                'oci_config_profile': 'DEFAULT'
            },
        })
    assert oci_utils.oci_config.get_profiles_in_use() == [
        (None, 'TOKEN'),
        ('us-ashburn-1', 'ASHBURN'),
        ('eu-frankfurt-1', 'DEFAULT'),
    ]


# ---------------------------------------------------------------------------
# Availability-domain prefix
# ---------------------------------------------------------------------------

_COMPARTMENT = 'ocid1.compartment.oc1..aaaaaaaalaunchhere'
_OTHER_COMPARTMENT = 'ocid1.compartment.oc1..aaaaaaaaother'


def _availability_domain(name):
    ad = mock.MagicMock()
    ad.name = name
    return ad


@pytest.fixture(name='ad_prefix_lookup')
def fixture_ad_prefix_lookup(monkeypatch):
    """Stubs the compartment and identity lookups behind the AD prefix.

    Returns the mocked identity client; `find_compartment` maps
    'us-phoenix-1' to `_COMPARTMENT` and every other region to
    `_OTHER_COMPARTMENT`, whose tenancies have different prefixes.
    """
    pytest.importorskip('oci')
    monkeypatch.setattr(oci_cloud, '_ad_prefixes', {})
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'TOKEN')
    # `query_helper` is an instance, so patch with a plain callable.
    monkeypatch.setattr(
        oci_cloud.query_helper, 'find_compartment', lambda region: _COMPARTMENT
        if region == 'us-phoenix-1' else _OTHER_COMPARTMENT)
    client = mock.MagicMock()

    def _list_availability_domains(compartment_id):
        response = mock.MagicMock()
        if compartment_id == _COMPARTMENT:
            response.data = [_availability_domain('Uocm:PHX-AD-1')]
        else:
            response.data = [_availability_domain('Other:US-ASHBURN-AD-1')]
        return response

    client.list_availability_domains.side_effect = _list_availability_domains
    monkeypatch.setattr(oci_adaptor,
                        'get_identity_client',
                        lambda region=None, profile='DEFAULT': client)
    return client


def test_availability_domain_prefix_comes_from_launch_compartment(
        ad_prefix_lookup):
    # pylint: disable=protected-access
    prefix = oci_cloud._get_availability_domain_prefix('us-phoenix-1')

    assert prefix == 'Uocm'
    # The prefix is tenancy-specific and must match the tenancy that owns the
    # launch compartment, not the profile's home tenancy.
    ad_prefix_lookup.list_availability_domains.assert_called_once_with(
        compartment_id=_COMPARTMENT)


def test_availability_domain_prefix_is_cached_per_compartment(ad_prefix_lookup):
    # A launch into a compartment of another tenancy must not reuse the
    # prefix cached for the first one, and the same compartment is only
    # looked up once per process.
    # pylint: disable=protected-access
    assert oci_cloud._get_availability_domain_prefix('us-phoenix-1') == 'Uocm'
    assert oci_cloud._get_availability_domain_prefix('us-ashburn-1') == 'Other'
    assert oci_cloud._get_availability_domain_prefix('us-phoenix-1') == 'Uocm'
    assert oci_cloud._get_availability_domain_prefix('us-ashburn-1') == 'Other'
    assert ad_prefix_lookup.list_availability_domains.call_args_list == [
        mock.call(compartment_id=_COMPARTMENT),
        mock.call(compartment_id=_OTHER_COMPARTMENT),
    ]


def test_availability_domain_lookups_for_different_compartments_overlap(
        ad_prefix_lookup):
    # The cache lock must not be held across the network call: a launch into
    # another compartment starts its own lookup while the first is still in
    # flight. The first lookup blocks until the second has started, so the
    # test deadlocks (and times out below) if the two are serialised.
    first_started = threading.Event()
    second_started = threading.Event()

    def _list_availability_domains(compartment_id):
        response = mock.MagicMock()
        if compartment_id == _COMPARTMENT:
            first_started.set()
            assert second_started.wait(timeout=5), (
                'the second lookup waited for the first one to finish')
            response.data = [_availability_domain('Uocm:PHX-AD-1')]
        else:
            second_started.set()
            response.data = [_availability_domain('Other:US-ASHBURN-AD-1')]
        return response

    ad_prefix_lookup.list_availability_domains.side_effect = (
        _list_availability_domains)

    # pylint: disable=protected-access
    with futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(oci_cloud._get_availability_domain_prefix,
                            'us-phoenix-1')
        assert first_started.wait(timeout=5)
        second = pool.submit(oci_cloud._get_availability_domain_prefix,
                             'us-ashburn-1')
        assert second.result(timeout=10) == 'Other'
        assert first.result(timeout=10) == 'Uocm'
    assert oci_cloud._ad_prefixes == {
        _COMPARTMENT: 'Uocm',
        _OTHER_COMPARTMENT: 'Other',
    }


def test_availability_domain_prefix_is_none_without_valid_config(monkeypatch):
    oci_sdk = pytest.importorskip('oci')
    monkeypatch.setattr(oci_cloud, '_ad_prefixes', {})
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'TOKEN')
    monkeypatch.setattr(oci_cloud.query_helper, 'find_compartment',
                        lambda region: _COMPARTMENT)

    def _raise(region=None, profile='DEFAULT'):
        raise oci_sdk.exceptions.ConfigFileNotFound('no config')

    monkeypatch.setattr(oci_adaptor, 'get_identity_client', _raise)
    # pylint: disable=protected-access
    assert oci_cloud._get_availability_domain_prefix('us-phoenix-1') is None

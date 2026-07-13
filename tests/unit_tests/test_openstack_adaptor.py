"""Tests for the OpenStack SDK adaptor."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock


def _import_adaptor():
    return importlib.import_module('sky.adaptors.openstack')


def test_import_sky_and_adaptor_do_not_import_openstacksdk():
    result = subprocess.run(
        [
            sys.executable, '-c',
            'import sys; import sky; import sky.adaptors.openstack; '
            'assert \'openstack\' not in sys.modules'
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_get_connection_uses_named_cloud_and_region():
    adaptor = _import_adaptor()
    connect = mock.Mock(return_value=object())

    with mock.patch.object(adaptor, 'openstack',
                           SimpleNamespace(connect=connect)):
        connection = adaptor.get_connection('lab', region='RegionOne')

    assert connection is connect.return_value
    connect.assert_called_once_with(cloud='lab', region_name='RegionOne')


def test_get_connection_allows_sdk_default_region():
    adaptor = _import_adaptor()
    connect = mock.Mock(return_value=object())

    with mock.patch.object(adaptor, 'openstack',
                           SimpleNamespace(connect=connect)):
        adaptor.get_connection('lab')

    connect.assert_called_once_with(cloud='lab')


def test_get_cloud_config_uses_selected_profile():
    adaptor = _import_adaptor()
    cloud_config = object()
    loader = mock.Mock()
    loader.get_one.return_value = cloud_config
    openstack = SimpleNamespace(config=SimpleNamespace(
        OpenStackConfig=mock.Mock(return_value=loader)))

    with mock.patch.object(adaptor, 'openstack', openstack):
        result = adaptor.get_cloud_config('lab', region='RegionOne')

    assert result is cloud_config
    loader.get_one.assert_called_once_with(cloud='lab', region_name='RegionOne')


def test_credential_mounts_include_config_secure_and_referenced_certificates(
        tmp_path: Path):
    adaptor = _import_adaptor()
    clouds_yaml = tmp_path / 'clouds.yaml'
    secure_yaml = tmp_path / 'secure.yaml'
    clouds_public_yaml = tmp_path / 'clouds-public.yaml'
    ca = tmp_path / 'ca.pem'
    cert = tmp_path / 'client.pem'
    key = tmp_path / 'client-key.pem'
    auth_cert = tmp_path / 'auth-client.pem'
    for path in (clouds_yaml, secure_yaml, clouds_public_yaml, ca, cert, key,
                 auth_cert):
        path.touch()

    loader = SimpleNamespace(config_filename=str(clouds_yaml),
                             secure_config_filename=str(secure_yaml))
    cloud_config = SimpleNamespace(
        config={
            'cacert': str(ca),
            'cert': str(cert),
            'key': str(key),
            'auth': {
                'client_cert': str(auth_cert),
                'password': 'must-not-be-returned',
            },
        })

    with mock.patch.object(adaptor,
                           '_load_cloud_config',
                           return_value=(loader, cloud_config)):
        mounts = adaptor.get_credential_file_mounts('lab', region='RegionOne')

    assert mounts['~/.config/openstack/clouds.yaml'] == str(clouds_yaml)
    assert mounts['~/.config/openstack/secure.yaml'] == str(secure_yaml)
    assert mounts['~/.config/openstack/clouds-public.yaml'] == str(
        clouds_public_yaml)
    for path in (ca, cert, key, auth_cert):
        assert mounts[str(path)] == str(path)
    assert all('must-not-be-returned' not in value
               for value in (*mounts.keys(), *mounts.values()))


def test_credential_mounts_skip_missing_referenced_files(tmp_path: Path):
    adaptor = _import_adaptor()
    clouds_yaml = tmp_path / 'clouds.yaml'
    clouds_yaml.touch()
    missing_ca = tmp_path / 'missing-ca.pem'
    loader = SimpleNamespace(config_filename=str(clouds_yaml),
                             secure_config_filename=None)
    cloud_config = SimpleNamespace(config={'cacert': str(missing_ca)})

    with mock.patch.object(adaptor,
                           '_load_cloud_config',
                           return_value=(loader, cloud_config)):
        mounts = adaptor.get_credential_file_mounts('lab')

    assert mounts == {'~/.config/openstack/clouds.yaml': str(clouds_yaml)}
    assert not os.path.exists(missing_ca)


def test_credential_mounts_use_sdk_loader_file_lists(tmp_path: Path):
    adaptor = _import_adaptor()
    clouds_yaml = tmp_path / 'clouds.yaml'
    secure_yaml = tmp_path / 'secure.yaml'
    vendor_yaml = tmp_path / 'clouds-public.yaml'
    for path in (clouds_yaml, secure_yaml, vendor_yaml):
        path.touch()

    loader = SimpleNamespace(config_filename=None,
                             secure_config_filename=None,
                             _config_files=[str(clouds_yaml)],
                             _secure_files=[str(secure_yaml)],
                             _vendor_files=[str(vendor_yaml)])
    cloud_config = SimpleNamespace(config={})

    with mock.patch.object(adaptor,
                           '_load_cloud_config',
                           return_value=(loader, cloud_config)):
        mounts = adaptor.get_credential_file_mounts('lab')

    assert mounts == {
        '~/.config/openstack/clouds.yaml': str(clouds_yaml),
        '~/.config/openstack/secure.yaml': str(secure_yaml),
        '~/.config/openstack/clouds-public.yaml': str(vendor_yaml),
    }


def test_credential_mounts_fall_back_to_config_environment_variables(
        tmp_path: Path):
    adaptor = _import_adaptor()
    clouds_yaml = tmp_path / 'custom-clouds.yaml'
    secure_yaml = tmp_path / 'custom-secure.yaml'
    clouds_yaml.touch()
    secure_yaml.touch()
    loader = SimpleNamespace()
    cloud_config = SimpleNamespace(config={})

    with mock.patch.dict(
            os.environ, {
                'OS_CLIENT_CONFIG_FILE': str(clouds_yaml),
                'OS_CLIENT_SECURE_FILE': str(secure_yaml),
            }), mock.patch.object(adaptor,
                                  '_load_cloud_config',
                                  return_value=(loader, cloud_config)):
        mounts = adaptor.get_credential_file_mounts('lab')

    assert mounts == {
        '~/.config/openstack/clouds.yaml': str(clouds_yaml),
        '~/.config/openstack/secure.yaml': str(secure_yaml),
    }


def test_credential_mounts_follow_loader_search_paths(tmp_path: Path):
    adaptor = _import_adaptor()
    config_dir = tmp_path / 'config'
    secure_dir = tmp_path / 'secure'
    vendor_dir = tmp_path / 'vendor'
    for directory in (config_dir, secure_dir, vendor_dir):
        directory.mkdir()
    clouds_yaml = config_dir / 'clouds.yaml'
    secure_yaml = secure_dir / 'secure.yaml'
    clouds_public_yaml = vendor_dir / 'clouds-public.yaml'
    for path in (clouds_yaml, secure_yaml, clouds_public_yaml):
        path.touch()

    loader = SimpleNamespace(
        config_filename=str(clouds_yaml),
        _secure_files=[str(secure_yaml)],
        _vendor_files=[str(clouds_public_yaml)],
        cloud_config={'clouds': {
            'lab': {
                'profile': 'vendor-profile'
            }
        }},
    )
    cloud_config = SimpleNamespace(config={})

    with mock.patch.object(adaptor,
                           '_load_cloud_config',
                           return_value=(loader, cloud_config)):
        mounts = adaptor.get_credential_file_mounts('lab')

    assert mounts == {
        '~/.config/openstack/clouds.yaml': str(clouds_yaml),
        '~/.config/openstack/secure.yaml': str(secure_yaml),
        '~/.config/openstack/clouds-public.yaml': str(clouds_public_yaml),
    }

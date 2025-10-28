"""Tests for Mithril cloud provider."""
from pathlib import Path
import tempfile

import pytest

from sky import clouds
from sky.clouds import mithril


def test_mithril_cloud_basics():
    cloud = mithril.Mithril()
    assert cloud.name == 'mithril'
    assert cloud._REPR == 'Mithril'
    assert cloud._MAX_CLUSTER_NAME_LEN_LIMIT == 120


def test_mithril_credential_file_mounts():
    with tempfile.TemporaryDirectory() as tmpdir:
        cred_path = Path(tmpdir) / 'config.yaml'
        cred_path.touch()
        with pytest.MonkeyPatch.context() as m:
            m.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(cred_path))
            cloud = mithril.Mithril()
            mounts = cloud.get_credential_file_mounts()
            assert str(cred_path) in mounts
            assert mounts[str(cred_path)] == '~/.flow/config.yaml'


def test_mithril_unsupported_features():
    cloud = mithril.Mithril()
    for feature in clouds.CloudImplementationFeatures:
        if feature in cloud._CLOUD_UNSUPPORTED_FEATURES:
            assert isinstance(cloud._CLOUD_UNSUPPORTED_FEATURES[feature], str)
        else:
            assert feature not in cloud._CLOUD_UNSUPPORTED_FEATURES


def test_mithril_region_zone_validation_disallows_zones():
    cloud = mithril.Mithril()
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone('some-region', 'zone-1')


def test_mithril_check_credentials_missing(monkeypatch, tmp_path):
    cloud = mithril.Mithril()
    fake_path = tmp_path / 'config.yaml'
    monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(fake_path))
    valid, msg = cloud._check_credentials()
    assert not valid
    assert 'Mithril credentials not found' in msg


def test_mithril_check_credentials_present(monkeypatch, tmp_path):
    cloud = mithril.Mithril()
    cred_path = tmp_path / 'config.yaml'
    cred_path.write_text('api_key: test-key')
    monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(cred_path))
    valid, msg = cloud._check_credentials()
    assert valid
    assert msg is None

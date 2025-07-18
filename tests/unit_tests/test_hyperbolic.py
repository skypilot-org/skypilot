"""Tests for Hyperbolic cloud provider."""
import os
from pathlib import Path
import tempfile

import pytest

from sky import clouds
from sky.clouds import hyperbolic
from sky.resources import Resources


def test_hyperbolic_cloud():
    """Test basic Hyperbolic cloud functionality."""
    cloud = hyperbolic.Hyperbolic()
    assert cloud.name == 'hyperbolic'
    assert cloud._REPR == 'Hyperbolic'
    assert cloud._MAX_CLUSTER_NAME_LEN_LIMIT == 120


def test_hyperbolic_credential_file_mounts():
    """Test credential file mounts for Hyperbolic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        api_key_path = Path(tmpdir) / 'api_key'
        api_key_path.touch()
        with pytest.MonkeyPatch.context() as m:
            m.setattr(hyperbolic.Hyperbolic, 'API_KEY_PATH', str(api_key_path))
            cloud = hyperbolic.Hyperbolic()
            mounts = cloud.get_credential_file_mounts()
            assert str(api_key_path) in mounts
            assert mounts[str(api_key_path)] == '~/.hyperbolic/api_key'


def test_hyperbolic_unsupported_features():
    cloud = hyperbolic.Hyperbolic()
    for feature in clouds.CloudImplementationFeatures:
        if feature in cloud._CLOUD_UNSUPPORTED_FEATURES:
            assert isinstance(cloud._CLOUD_UNSUPPORTED_FEATURES[feature], str)
        else:
            assert feature not in cloud._CLOUD_UNSUPPORTED_FEATURES


def test_hyperbolic_region_zone_validation():
    cloud = hyperbolic.Hyperbolic()
    region, zone = cloud.validate_region_zone('default', None)
    assert region == 'default'
    assert zone is None
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone('default', 'zone-1')


def test_hyperbolic_resource_feasibility():
    """Test resource feasibility for Hyperbolic."""
    cloud = hyperbolic.Hyperbolic()
    resources = Resources(cloud=cloud,
                          instance_type='1x-H100-28-271',
                          accelerators={'H100': 1})
    feasible = cloud._get_feasible_launchable_resources(resources)
    assert hasattr(feasible, 'resources_list')
    assert hasattr(feasible, 'fuzzy_candidate_list')


# Additional tests for error handling and wrapper functions


def test_hyperbolic_check_credentials_missing(monkeypatch, tmp_path):
    cloud = hyperbolic.Hyperbolic()
    fake_path = tmp_path / 'api_key'
    monkeypatch.setattr(os.path, 'expanduser', lambda x: str(fake_path))
    valid, msg = cloud._check_credentials()
    assert not valid
    assert 'API key not found' in msg


def test_hyperbolic_check_credentials_present(monkeypatch, tmp_path):
    """Test credential check when API key is present."""
    cloud = hyperbolic.Hyperbolic()
    api_key_path = tmp_path / 'api_key'
    api_key_path.write_text('test-key')

    # Monkeypatch both the API_KEY_PATH and os.path.expanduser
    monkeypatch.setattr(hyperbolic.Hyperbolic, 'API_KEY_PATH',
                        str(api_key_path))
    monkeypatch.setattr(os.path, 'expanduser', lambda x: str(api_key_path))

    valid, msg = cloud._check_credentials()
    assert valid
    assert msg is None

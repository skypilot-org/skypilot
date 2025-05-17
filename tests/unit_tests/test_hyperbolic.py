"""Unit tests for Hyperbolic cloud provider."""
import os
import tempfile
from unittest import mock

import pytest

from sky import clouds
from sky.clouds import hyperbolic


def test_hyperbolic_cloud():
    """Test basic Hyperbolic cloud functionality."""
    cloud = hyperbolic.Hyperbolic()
    assert cloud.name == 'hyperbolic'
    assert cloud._REPR == 'Hyperbolic'
    assert cloud._MAX_CLUSTER_NAME_LEN_LIMIT == 120


def test_hyperbolic_credential_file_mounts(tmp_path):
    cloud = hyperbolic.Hyperbolic()
    api_key_path = tmp_path / 'api_key'
    api_key_path.write_text('test-key')
    with mock.patch('os.path.expanduser', return_value=str(api_key_path)):
        mounts = cloud.get_credential_file_mounts()
        assert str(api_key_path) in mounts.values()
    with mock.patch('os.path.expanduser', return_value='/nonexistent/api_key'):
        mounts = cloud.get_credential_file_mounts()
        assert mounts == {}


def test_hyperbolic_unsupported_features():
    cloud = hyperbolic.Hyperbolic()
    for feature in clouds.CloudImplementationFeatures:
        if feature in cloud._CLOUD_UNSUPPORTED_FEATURES:
            assert isinstance(cloud._CLOUD_UNSUPPORTED_FEATURES[feature], str)
        else:
            assert feature not in cloud._CLOUD_UNSUPPORTED_FEATURES


def test_hyperbolic_region_zone_validation():
    cloud = hyperbolic.Hyperbolic()
    region, zone = cloud.validate_region_zone('us-central1', None)
    assert region == 'us-central1'
    assert zone is None
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone('us-central1', 'zone-1')


def test_hyperbolic_resource_feasibility():
    cloud = hyperbolic.Hyperbolic()
    resources = clouds.Resources(cloud=cloud,
                                 instance_type='1x-T4-4-17',
                                 accelerators={'T4': 1})
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
    cloud = hyperbolic.Hyperbolic()
    api_key_path = tmp_path / 'api_key'
    api_key_path.write_text('test-key')
    monkeypatch.setattr(os.path, 'expanduser', lambda x: str(api_key_path))
    valid, msg = cloud._check_credentials()
    assert valid
    assert msg is None

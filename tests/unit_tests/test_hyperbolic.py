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


def test_hyperbolic_credentials():
    """Test Hyperbolic credential checking."""
    cloud = hyperbolic.Hyperbolic()

    # Test when credentials don't exist
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch('os.path.expanduser',
                        return_value=os.path.join(tmpdir, 'api_key')):
            valid, msg = cloud._check_credentials()
            assert not valid
            assert 'API key not found' in msg

    # Test when credentials exist
    with tempfile.TemporaryDirectory() as tmpdir:
        api_key_path = os.path.join(tmpdir, 'api_key')
        with open(api_key_path, 'w') as f:
            f.write('test-key')
        with mock.patch('os.path.expanduser', return_value=api_key_path):
            valid, msg = cloud._check_credentials()
            assert valid
            assert msg is None


def test_hyperbolic_features():
    """Test Hyperbolic feature support."""
    cloud = hyperbolic.Hyperbolic()
    features = cloud._CLOUD_UNSUPPORTED_FEATURES

    # Check that expected features are marked as unsupported
    assert clouds.CloudImplementationFeatures.STOP in features
    assert clouds.CloudImplementationFeatures.MULTI_NODE in features
    assert clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER in features
    assert clouds.CloudImplementationFeatures.STORAGE_MOUNTING in features
    assert clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS in features
    assert clouds.CloudImplementationFeatures.SPOT_INSTANCE in features


def test_hyperbolic_regions():
    """Test Hyperbolic region handling."""
    cloud = hyperbolic.Hyperbolic()

    # Test region validation
    region, zone = cloud.validate_region_zone('us-central1', None)
    assert region == 'us-central1'
    assert zone is None

    # Test that zones are not supported
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone('us-central1', 'zone-1')


def test_hyperbolic_resources():
    """Test Hyperbolic resource handling."""
    cloud = hyperbolic.Hyperbolic()

    # Test resource feasibility
    resources = clouds.Resources(cloud=cloud,
                                 instance_type='1x-T4-4-17',
                                 accelerators={'T4': 1})
    feasible = cloud._get_feasible_launchable_resources(resources)
    assert feasible.resources_list == []
    assert feasible.fuzzy_candidate_list == []
    assert 'Not implemented for Hyperbolic' in feasible.hint

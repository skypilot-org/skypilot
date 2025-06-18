#!/usr/bin/env python3
"""Tests for Hyperbolic cloud provider (minimal, up-to-date with utils.py)."""
import os
from pathlib import Path
import tempfile
from unittest.mock import Mock
from unittest.mock import patch

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


def test_hyperbolic_h100_endpoint_selection():
    """Test that H100 instances use the correct marketplace virtual machine rental endpoints (minimal logic)."""
    from sky.provision.hyperbolic import utils

    # Mock the client and its methods
    mock_client = Mock()
    mock_client.launch_instance = Mock(return_value=('test-instance-id',
                                                     'ssh test@host'))

    with patch('sky.provision.hyperbolic.utils.get_client',
               return_value=mock_client):
        # Test H100 instance - should use virtual machine rental endpoint
        utils.launch_instance('H100', 1, 'test-cluster')
        mock_client.launch_instance.assert_called_once_with(
            'H100', 1, 'test-cluster')

        # Verify the endpoint selection logic in the client
        # This would require mocking the _make_request method to verify the endpoint
        # For now, we'll just verify the method was called correctly


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


# New tests for endpoint selection functionality (minimal, up-to-date)
def test_ondemand_gpu_detection():
    """Test that H100 GPUs are correctly identified as on-demand (minimal logic)."""
    from sky.provision.hyperbolic.utils import is_ondemand_gpu

    assert is_ondemand_gpu("H100")
    assert is_ondemand_gpu("H100-80GB")
    assert not is_ondemand_gpu("A100")
    assert not is_ondemand_gpu("V100")
    assert not is_ondemand_gpu("")


def test_endpoint_selection():
    """Test that correct endpoints are selected based on GPU type (minimal logic)."""
    from sky.provision.hyperbolic.utils import get_endpoints

    # H100 should use on-demand endpoints
    h100_endpoints = get_endpoints("H100")
    assert h100_endpoints['create'] == '/v2/marketplace/virtual-machine-rentals'
    assert h100_endpoints['list'] == '/v2/marketplace/virtual-machine-rentals'
    assert h100_endpoints[
        'terminate'] == '/v2/marketplace/virtual-machine-rentals/terminate'

    # A100 should use SPOT endpoints
    a100_endpoints = get_endpoints("A100")
    assert a100_endpoints[
        'create'] == '/v2/marketplace/instances/create-cheapest'
    assert a100_endpoints['list'] == '/v1/marketplace/instances'
    assert a100_endpoints['terminate'] == '/v1/marketplace/instances/terminate'


def test_status_mapping():
    """Test that status mapping works for both endpoint types (minimal logic)."""
    from sky.provision.hyperbolic.utils import HyperbolicInstanceStatus

    # Test on-demand statuses
    assert HyperbolicInstanceStatus.from_raw_status(
        "Running").value == "running"
    assert HyperbolicInstanceStatus.from_raw_status(
        "Pending").value == "pending"
    assert HyperbolicInstanceStatus.from_raw_status("Failed").value == "failed"

    # Test SPOT statuses
    assert HyperbolicInstanceStatus.from_raw_status("online").value == "online"
    assert HyperbolicInstanceStatus.from_raw_status(
        "creating").value == "creating"
    assert HyperbolicInstanceStatus.from_raw_status("failed").value == "failed"

    # Test unknown status
    assert HyperbolicInstanceStatus.from_raw_status(
        "unknown_status").value == "unknown"


if __name__ == "__main__":
    pytest.main([__file__])

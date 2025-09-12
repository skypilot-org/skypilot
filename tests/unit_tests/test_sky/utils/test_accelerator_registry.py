"""Unit tests for accelerator_registry.py."""
import unittest.mock as mock

import pandas as pd
import pytest

from sky.utils import accelerator_registry


class MockCloud:
    """Mock cloud class for testing."""

    def __init__(self, name: str):
        self.name = name

    def __str__(self):
        return self.name


@pytest.fixture
def mock_accelerator_df():
    """Mock accelerator dataframe for testing."""
    return pd.DataFrame({
        'AcceleratorName': ['V100', 'A100', 'T4', 'CustomGPU'],
        'Clouds': ['AWS,GCP', 'AWS,GCP,Azure', 'AWS,GCP', 'Kubernetes']
    })


@pytest.fixture
def setup_mocks(monkeypatch, mock_accelerator_df):
    """Setup common mocks for accelerator registry tests."""
    monkeypatch.setattr(accelerator_registry, '_accelerator_df',
                        mock_accelerator_df)


class TestCanonicalizeAcceleratorName:
    """Test cases for canonicalize_accelerator_name function."""

    def test_tpu_lowercase_conversion(self, setup_mocks):
        """Test that TPU names are always converted to lowercase."""
        result = accelerator_registry.canonicalize_accelerator_name(
            'TPU-V3', None)
        assert result == 'tpu-v3'

        result = accelerator_registry.canonicalize_accelerator_name(
            'tpu-V4', MockCloud('GCP'))
        assert result == 'tpu-v4'

    def test_exact_match_found_in_catalog(self, setup_mocks):
        """Test exact match returns the canonical name from catalog."""
        result = accelerator_registry.canonicalize_accelerator_name(
            'v100', None)
        assert result == 'V100'

        result = accelerator_registry.canonicalize_accelerator_name(
            'A100', MockCloud('AWS'))
        assert result == 'A100'

    def test_cloud_specific_filtering(self, setup_mocks):
        """Test that cloud-specific filtering works correctly."""
        # T4 is available on AWS and GCP, but not Azure
        result = accelerator_registry.canonicalize_accelerator_name(
            't4', MockCloud('AWS'))
        assert result == 'T4'

        # When no cloud specified, should still find matches
        result = accelerator_registry.canonicalize_accelerator_name('t4', None)
        assert result == 'T4'

    def test_single_match_returned(self, setup_mocks):
        """Test that single match is returned when found."""
        result = accelerator_registry.canonicalize_accelerator_name(
            'custom', MockCloud('Kubernetes'))
        assert result == 'CustomGPU'

    def test_ambiguous_accelerator_error(self, setup_mocks, monkeypatch):
        """Test that ambiguous accelerator names raise ValueError."""
        # Create a dataframe with ambiguous matches
        ambiguous_df = pd.DataFrame({
            'AcceleratorName': ['GPU1', 'GPU2', 'GPU3'],
            'Clouds': ['AWS', 'GCP', 'Azure']
        })
        monkeypatch.setattr(accelerator_registry, '_accelerator_df',
                            ambiguous_df)

        with pytest.raises(ValueError, match='is ambiguous'):
            accelerator_registry.canonicalize_accelerator_name('gpu', None)

    @mock.patch('sky.catalog.list_accelerators')
    def test_kubernetes_lookup_called_when_not_found(self, mock_list_accel,
                                                     setup_mocks):
        """Test Kubernetes.list_accelerators is called when accelerator 
        not found."""
        mock_list_accel.return_value = {'custom-k8s-gpu': {}}

        result = accelerator_registry.canonicalize_accelerator_name(
            'custom-k8s-gpu', MockCloud('Kubernetes'))

        # Verify that catalog.list_accelerators was called with correct params
        mock_list_accel.assert_called_once_with(name_filter='custom-k8s-gpu',
                                                case_sensitive=False,
                                                clouds='Kubernetes')
        assert result == 'custom-k8s-gpu'

    @mock.patch('sky.catalog.list_accelerators')
    def test_kubernetes_lookup_called_when_cloud_none(self, mock_list_accel,
                                                      setup_mocks):
        """Test Kubernetes lookup is called when cloud is None."""
        mock_list_accel.return_value = {'unknown-gpu': {}}

        result = accelerator_registry.canonicalize_accelerator_name(
            'unknown-gpu', None)

        # Verify that catalog.list_accelerators was called
        mock_list_accel.assert_called_once_with(name_filter='unknown-gpu',
                                                case_sensitive=False,
                                                clouds='Kubernetes')
        assert result == 'unknown-gpu'

    @mock.patch('sky.catalog.list_accelerators')
    def test_kubernetes_lookup_not_called_for_other_clouds(
            self, mock_list_accel, setup_mocks):
        """Test Kubernetes lookup is NOT called for non-Kubernetes clouds."""
        result = accelerator_registry.canonicalize_accelerator_name(
            'unknown-gpu', MockCloud('AWS'))

        # Verify that catalog.list_accelerators was NOT called
        mock_list_accel.assert_not_called()
        assert result == 'unknown-gpu'

    @mock.patch('sky.catalog.list_accelerators')
    def test_kubernetes_lookup_returns_original_when_not_found(
            self, mock_list_accel, setup_mocks):
        """Test original name returned when not found in Kubernetes."""
        mock_list_accel.return_value = {}

        result = accelerator_registry.canonicalize_accelerator_name(
            'nonexistent-gpu', MockCloud('Kubernetes'))

        mock_list_accel.assert_called_once()
        assert result == 'nonexistent-gpu'

    @mock.patch('sky.catalog.list_accelerators')
    def test_only_kubernetes_list_accelerators_called(self, mock_list_accel,
                                                      setup_mocks):
        """Test that ONLY Kubernetes.list_accelerators is called, 
        when a non-canonical name is provided."""
        mock_list_accel.return_value = {'test-gpu': {}}

        # Call the function
        accelerator_registry.canonicalize_accelerator_name(
            'test-gpu', MockCloud('Kubernetes'))

        # Verify exactly one call to catalog.list_accelerators with
        # Kubernetes cloud
        assert mock_list_accel.call_count == 1
        call_args = mock_list_accel.call_args
        assert call_args[1]['clouds'] == 'Kubernetes'
        assert call_args[1]['name_filter'] == 'test-gpu'
        assert call_args[1]['case_sensitive'] is False

    @mock.patch('sky.catalog.list_accelerators')
    def test_no_external_calls_when_other_cloud_specified(
            self, mock_list_accel, setup_mocks):
        """Test no external calls when accelerator found in local catalog."""
        result = accelerator_registry.canonicalize_accelerator_name(
            'test_gpu', MockCloud('AWS'))

        # Should not call list_accelerators since V100 is in catalog
        mock_list_accel.assert_not_called()
        assert result == 'test_gpu'

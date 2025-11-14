"""Unit tests for volume utilities."""

from unittest import mock

import pytest

from sky import models
from sky.utils import volume


class TestVolumeMount:
    """Test cases for VolumeMount class."""

    def test_resolve_ephemeral_config_valid_size_gi(self):
        """Test resolve_ephemeral_config with valid size in Gi."""
        path = '/data'
        config = {
            'size': '10Gi',
            'type': 'k8s-pvc',
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.path == path
        assert volume_mount.volume_name == ''
        assert volume_mount.is_ephemeral is True
        assert volume_mount.volume_config.size == '10'
        assert volume_mount.volume_config.type == 'k8s-pvc'
        assert volume_mount.volume_config.cloud == 'kubernetes'

    def test_resolve_ephemeral_config_valid_size_ti(self):
        """Test resolve_ephemeral_config with valid size in Ti."""
        path = '/data'
        config = {
            'size': '1Ti',
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.path == path
        assert volume_mount.is_ephemeral is True
        assert volume_mount.volume_config.size == '1024'

    def test_resolve_ephemeral_config_valid_size_plain(self):
        """Test resolve_ephemeral_config with valid plain number size."""
        path = '/data'
        config = {
            'size': '100',
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.path == path
        assert volume_mount.is_ephemeral is True
        assert volume_mount.volume_config.size == '100'

    def test_resolve_ephemeral_config_with_labels(self):
        """Test resolve_ephemeral_config with labels."""
        path = '/data'
        config = {
            'size': '10Gi',
            'type': 'k8s-pvc',
            'labels': {
                'env': 'test',
                'app': 'myapp'
            }
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.volume_config.labels == {
            'env': 'test',
            'app': 'myapp'
        }

    def test_resolve_ephemeral_config_with_config(self):
        """Test resolve_ephemeral_config with additional config."""
        path = '/data'
        config = {
            'size': '10Gi',
            'type': 'k8s-pvc',
            'config': {
                'storage_class': 'fast-ssd'
            }
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.volume_config.config == {
            'storage_class': 'fast-ssd'
        }

    def test_resolve_ephemeral_config_without_type(self):
        """Test resolve_ephemeral_config without explicit type."""
        path = '/data'
        config = {
            'size': '10Gi',
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.volume_config.type == ''
        assert volume_mount.is_ephemeral is True

    def test_resolve_ephemeral_config_missing_size(self):
        """Test resolve_ephemeral_config with missing size."""
        path = '/data'
        config = {
            'type': 'k8s-pvc',
        }

        with pytest.raises(ValueError) as exc_info:
            volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert 'Volume size must be specified' in str(exc_info.value)

    def test_resolve_ephemeral_config_invalid_volume_type(self):
        """Test resolve_ephemeral_config with unsupported volume type."""
        path = '/data'
        config = {
            'size': '10Gi',
            'type': 'invalid-type',
        }

        with pytest.raises(ValueError) as exc_info:
            volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert 'Unsupported ephemeral volume type' in str(exc_info.value)
        assert 'invalid-type' in str(exc_info.value)

    def test_resolve_ephemeral_config_zero_size(self):
        """Test resolve_ephemeral_config with zero size."""
        path = '/data'
        config = {
            'size': '0',
        }

        with pytest.raises(ValueError) as exc_info:
            volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert 'Size must be no less than 1Gi' in str(exc_info.value)

    def test_resolve_ephemeral_config_invalid_size_format(self):
        """Test resolve_ephemeral_config with invalid size format."""
        path = '/data'
        config = {
            'size': 'invalid',
        }

        with pytest.raises(ValueError) as exc_info:
            volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert 'Invalid size' in str(exc_info.value)
        assert 'invalid' in str(exc_info.value)

    def test_resolve_ephemeral_config_small_size_mi(self):
        """Test resolve_ephemeral_config with size in Mi (should fail)."""
        path = '/data'
        config = {
            'size': '500Mi',
        }

        with pytest.raises(ValueError) as exc_info:
            volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert 'Invalid size' in str(exc_info.value)

    def test_resolve_ephemeral_config_case_insensitive_type(self):
        """Test resolve_ephemeral_config with uppercase type."""
        path = '/data'
        config = {
            'size': '10Gi',
            'type': 'K8S-PVC',  # uppercase
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.volume_config.type == 'K8S-PVC'
        assert volume_mount.is_ephemeral is True

    def test_resolve_ephemeral_config_complete(self):
        """Test resolve_ephemeral_config with all fields."""
        path = '/data'
        config = {
            'size': '50Gi',
            'type': 'k8s-pvc',
            'labels': {
                'team': 'ml',
                'project': 'training'
            },
            'config': {
                'storage_class': 'premium-ssd',
                'mount_options': ['rw', 'async']
            }
        }

        volume_mount = volume.VolumeMount.resolve_ephemeral_config(path, config)

        assert volume_mount.path == path
        assert volume_mount.volume_name == ''
        assert volume_mount.is_ephemeral is True
        assert volume_mount.volume_config.size == '50'
        assert volume_mount.volume_config.type == 'k8s-pvc'
        assert volume_mount.volume_config.cloud == 'kubernetes'
        assert volume_mount.volume_config.labels == {
            'team': 'ml',
            'project': 'training'
        }
        assert volume_mount.volume_config.config == {
            'storage_class': 'premium-ssd',
            'mount_options': ['rw', 'async']
        }
        assert volume_mount.volume_config.name == ''
        assert volume_mount.volume_config.region is None
        assert volume_mount.volume_config.zone is None
        assert volume_mount.volume_config.name_on_cloud == ''

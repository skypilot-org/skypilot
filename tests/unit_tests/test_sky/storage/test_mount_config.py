"""Tests for MountConfig and Storage config field functionality."""
import pytest

from sky.data import storage
from sky.data import mounting_utils


class TestMountConfig:
    """Tests for the MountConfig dataclass."""

    def test_mount_config_defaults(self):
        """Test MountConfig default values."""
        config = storage.MountConfig()
        assert config.readonly is False
        # Default is True for backward compatibility
        assert config.sequential_upload is True

    def test_mount_config_custom_values(self):
        """Test MountConfig with custom values."""
        config = storage.MountConfig(readonly=True, sequential_upload=False)
        assert config.readonly is True
        assert config.sequential_upload is False

    def test_mount_config_from_dict_none(self):
        """Test MountConfig.from_dict with None input."""
        config = storage.MountConfig.from_dict(None)
        assert config.readonly is False
        assert config.sequential_upload is True

    def test_mount_config_from_dict_empty(self):
        """Test MountConfig.from_dict with empty dict."""
        config = storage.MountConfig.from_dict({})
        assert config.readonly is False
        assert config.sequential_upload is True

    def test_mount_config_from_dict_readonly_only(self):
        """Test MountConfig.from_dict with only readonly set."""
        config = storage.MountConfig.from_dict({'readonly': True})
        assert config.readonly is True
        assert config.sequential_upload is True

    def test_mount_config_from_dict_sequential_upload_false(self):
        """Test MountConfig.from_dict with sequential_upload=False."""
        config = storage.MountConfig.from_dict({'sequential_upload': False})
        assert config.readonly is False
        assert config.sequential_upload is False

    def test_mount_config_from_dict_both_options(self):
        """Test MountConfig.from_dict with both options set."""
        config = storage.MountConfig.from_dict({
            'readonly': True,
            'sequential_upload': False
        })
        assert config.readonly is True
        assert config.sequential_upload is False

    def test_mount_config_from_dict_extra_fields_ignored(self):
        """Test MountConfig.from_dict ignores unknown fields."""
        config = storage.MountConfig.from_dict({
            'readonly': True,
            'unknown_field': 'value',
            'another_field': 123
        })
        assert config.readonly is True
        assert config.sequential_upload is True


class TestStorageFromYamlConfig:
    """Tests for Storage.from_yaml_config with mount config."""

    def test_storage_from_yaml_no_config(self):
        """Test Storage.from_yaml_config without config field."""
        yaml_config = {
            'name': 'test-bucket',
            'mode': 'MOUNT_CACHED',
        }
        storage_obj = storage.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_config.readonly is False
        assert storage_obj.mount_config.sequential_upload is True

    def test_storage_from_yaml_with_empty_config(self):
        """Test Storage.from_yaml_config with empty config field."""
        yaml_config = {
            'name': 'test-bucket',
            'mode': 'MOUNT_CACHED',
            'config': {},
        }
        storage_obj = storage.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_config.readonly is False
        assert storage_obj.mount_config.sequential_upload is True

    def test_storage_from_yaml_with_readonly_config(self):
        """Test Storage.from_yaml_config with readonly config."""
        yaml_config = {
            'name': 'test-bucket',
            'mode': 'MOUNT_CACHED',
            'config': {
                'readonly': True,
            },
        }
        storage_obj = storage.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_config.readonly is True
        assert storage_obj.mount_config.sequential_upload is True

    def test_storage_from_yaml_with_sequential_upload_false(self):
        """Test Storage.from_yaml_config with sequential_upload=False."""
        yaml_config = {
            'name': 'test-bucket',
            'mode': 'MOUNT_CACHED',
            'config': {
                'sequential_upload': False,
            },
        }
        storage_obj = storage.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_config.readonly is False
        assert storage_obj.mount_config.sequential_upload is False

    def test_storage_from_yaml_with_full_config(self):
        """Test Storage.from_yaml_config with both config options."""
        yaml_config = {
            'name': 'test-bucket',
            'mode': 'MOUNT_CACHED',
            'config': {
                'readonly': True,
                'sequential_upload': False,
            },
        }
        storage_obj = storage.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_config.readonly is True
        assert storage_obj.mount_config.sequential_upload is False


class TestStorageToYamlConfig:
    """Tests for Storage.to_yaml_config with mount config."""

    def test_storage_to_yaml_default_config(self):
        """Test Storage.to_yaml_config with default mount config."""
        storage_obj = storage.Storage(name='test-bucket')
        yaml_config = storage_obj.to_yaml_config()
        # Default config should not be included
        assert 'config' not in yaml_config

    def test_storage_to_yaml_with_readonly(self):
        """Test Storage.to_yaml_config with readonly config."""
        mount_config = storage.MountConfig(readonly=True)
        storage_obj = storage.Storage(name='test-bucket',
                                       mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' in yaml_config
        assert yaml_config['config']['readonly'] is True
        # sequential_upload=True is default, so not serialized
        assert 'sequential_upload' not in yaml_config['config']

    def test_storage_to_yaml_with_sequential_upload_false(self):
        """Test to_yaml_config with sequential_upload=False (non-default)."""
        mount_config = storage.MountConfig(sequential_upload=False)
        storage_obj = storage.Storage(name='test-bucket',
                                       mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' in yaml_config
        # sequential_upload=False is non-default, so it should be serialized
        assert yaml_config['config']['sequential_upload'] is False
        assert 'readonly' not in yaml_config['config']

    def test_storage_to_yaml_with_sequential_upload_true_not_serialized(self):
        """Test to_yaml_config doesn't serialize default sequential_upload."""
        mount_config = storage.MountConfig(sequential_upload=True)
        storage_obj = storage.Storage(name='test-bucket',
                                       mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        # sequential_upload=True is default, so no config should be present
        assert 'config' not in yaml_config

    def test_storage_to_yaml_with_full_config(self):
        """Test Storage.to_yaml_config with both non-default options."""
        mount_config = storage.MountConfig(readonly=True,
                                           sequential_upload=False)
        storage_obj = storage.Storage(name='test-bucket',
                                       mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' in yaml_config
        assert yaml_config['config']['readonly'] is True
        assert yaml_config['config']['sequential_upload'] is False


class TestMountCachedCmd:
    """Tests for get_mount_cached_cmd with mount config."""

    def test_mount_cached_cmd_default(self):
        """Test get_mount_cached_cmd with default config."""
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_config=None)
        # Default should include --transfers 1 for sequential uploads
        assert '--transfers 1' in cmd
        assert '--read-only' not in cmd

    def test_mount_cached_cmd_with_readonly(self):
        """Test get_mount_cached_cmd with readonly config."""
        mount_config = storage.MountConfig(readonly=True)
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_config=mount_config)
        assert '--read-only' in cmd
        assert '--transfers 1' in cmd  # Default sequential

    def test_mount_cached_cmd_with_sequential_upload_true(self):
        """Test get_mount_cached_cmd with sequential_upload=True (default)."""
        mount_config = storage.MountConfig(sequential_upload=True)
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_config=mount_config)
        assert '--transfers 1' in cmd
        assert '--read-only' not in cmd

    def test_mount_cached_cmd_with_sequential_upload_false(self):
        """Test get_mount_cached_cmd with sequential_upload=False."""
        mount_config = storage.MountConfig(sequential_upload=False)
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_config=mount_config)
        # Should NOT include --transfers 1 for parallel uploads
        assert '--transfers 1' not in cmd
        assert '--read-only' not in cmd

    def test_mount_cached_cmd_with_both_options(self):
        """Test get_mount_cached_cmd with readonly and parallel uploads."""
        mount_config = storage.MountConfig(readonly=True,
                                           sequential_upload=False)
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_config=mount_config)
        assert '--read-only' in cmd
        # parallel uploads (sequential_upload=False)
        assert '--transfers 1' not in cmd


class TestMountCmd:
    """Tests for mount commands (MOUNT mode) with mount config."""

    def test_s3_mount_cmd_default(self):
        """Test get_s3_mount_cmd with default config."""
        cmd = mounting_utils.get_s3_mount_cmd(
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            _bucket_sub_path=None,
            mount_config=None)
        assert '--read-only' not in cmd
        assert '-o ro' not in cmd

    def test_s3_mount_cmd_readonly(self):
        """Test get_s3_mount_cmd with readonly config."""
        mount_config = storage.MountConfig(readonly=True)
        cmd = mounting_utils.get_s3_mount_cmd(
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            _bucket_sub_path=None,
            mount_config=mount_config)
        # Should include readonly flag for both rclone (ARM) and goofys (x86)
        assert '--read-only' in cmd or '-o ro' in cmd

    def test_gcs_mount_cmd_default(self):
        """Test get_gcs_mount_cmd with default config."""
        cmd = mounting_utils.get_gcs_mount_cmd(
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            _bucket_sub_path=None,
            mount_config=None)
        assert '-o ro' not in cmd

    def test_gcs_mount_cmd_readonly(self):
        """Test get_gcs_mount_cmd with readonly config."""
        mount_config = storage.MountConfig(readonly=True)
        cmd = mounting_utils.get_gcs_mount_cmd(
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            _bucket_sub_path=None,
            mount_config=mount_config)
        assert '-o ro' in cmd

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
        assert config.sequential_upload is True  # default for backward compat

    @pytest.mark.parametrize('input_dict,expected_readonly,expected_seq', [
        (None, False, True),
        ({}, False, True),
        ({'readonly': True}, True, True),
        ({'sequential_upload': False}, False, False),
        ({'readonly': True, 'sequential_upload': False}, True, False),
        ({'readonly': True, 'unknown_field': 'value'}, True, True),
    ])
    def test_mount_config_from_dict(self, input_dict, expected_readonly,
                                    expected_seq):
        """Test MountConfig.from_dict with various inputs."""
        config = storage.MountConfig.from_dict(input_dict)
        assert config.readonly is expected_readonly
        assert config.sequential_upload is expected_seq


class TestStorageFromYamlConfig:
    """Tests for Storage.from_yaml_config with mount config."""

    @pytest.mark.parametrize('config_field,expected_readonly,expected_seq', [
        (None, False, True),
        ({}, False, True),
        ({'readonly': True}, True, True),
        ({'sequential_upload': False}, False, False),
        ({'readonly': True, 'sequential_upload': False}, True, False),
    ])
    def test_storage_from_yaml_config(self, config_field, expected_readonly,
                                      expected_seq):
        """Test Storage.from_yaml_config with various config fields."""
        yaml_config = {'name': 'test-bucket', 'mode': 'MOUNT_CACHED'}
        if config_field is not None:
            yaml_config['config'] = config_field
        storage_obj = storage.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_config.readonly is expected_readonly
        assert storage_obj.mount_config.sequential_upload is expected_seq


class TestStorageToYamlConfig:
    """Tests for Storage.to_yaml_config with mount config."""

    def test_default_config_not_serialized(self):
        """Test default mount config is not included in yaml."""
        storage_obj = storage.Storage(name='test-bucket')
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' not in yaml_config

    def test_sequential_upload_true_not_serialized(self):
        """Test sequential_upload=True (default) is not serialized."""
        mount_config = storage.MountConfig(sequential_upload=True)
        storage_obj = storage.Storage(name='test-bucket',
                                       mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' not in yaml_config

    @pytest.mark.parametrize('ro,seq,expect_ro,expect_seq', [
        (True, True, True, None),  # readonly only, seq is default
        (False, False, None, False),  # seq only, readonly is default
        (True, False, True, False),  # both non-default
    ])
    def test_non_default_config_serialized(self, ro, seq, expect_ro,
                                           expect_seq):
        """Test non-default mount config values are serialized."""
        mount_config = storage.MountConfig(readonly=ro, sequential_upload=seq)
        storage_obj = storage.Storage(name='test-bucket',
                                       mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' in yaml_config
        if expect_ro is not None:
            assert yaml_config['config']['readonly'] is expect_ro
        else:
            assert 'readonly' not in yaml_config['config']
        if expect_seq is not None:
            assert yaml_config['config']['sequential_upload'] is expect_seq
        else:
            assert 'sequential_upload' not in yaml_config['config']


class TestMountCachedCmd:
    """Tests for get_mount_cached_cmd with mount config."""

    def _get_cmd(self, mount_config=None):
        return mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_config=mount_config)

    @pytest.mark.parametrize('ro,seq,expect_ro,expect_seq', [
        (None, None, False, True),  # None config
        (False, True, False, True),  # Default config
        (True, True, True, True),  # readonly only
        (False, False, False, False),  # parallel only
        (True, False, True, False),  # both
    ])
    def test_mount_cached_cmd(self, ro, seq, expect_ro, expect_seq):
        """Test get_mount_cached_cmd with various configs."""
        if ro is None:
            mount_config = None
        else:
            mount_config = storage.MountConfig(readonly=ro,
                                               sequential_upload=seq)
        cmd = self._get_cmd(mount_config)
        assert ('--read-only' in cmd) == expect_ro
        assert ('--transfers 1' in cmd) == expect_seq


class TestMountCmd:
    """Tests for mount commands (MOUNT mode) with mount config."""

    @pytest.mark.parametrize('ro,expect_flag', [(False, False), (True, True)])
    def test_s3_mount_cmd(self, ro, expect_flag):
        """Test get_s3_mount_cmd with readonly config."""
        cfg = storage.MountConfig(readonly=ro) if ro else None
        cmd = mounting_utils.get_s3_mount_cmd(
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            _bucket_sub_path=None,
            mount_config=cfg)
        has_flag = '--read-only' in cmd or '-o ro' in cmd
        assert has_flag == expect_flag

    @pytest.mark.parametrize('ro,expect_flag', [(False, False), (True, True)])
    def test_gcs_mount_cmd(self, ro, expect_flag):
        """Test get_gcs_mount_cmd with readonly config."""
        cfg = storage.MountConfig(readonly=ro) if ro else None
        cmd = mounting_utils.get_gcs_mount_cmd(
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            _bucket_sub_path=None,
            mount_config=cfg)
        assert ('-o ro' in cmd) == expect_flag

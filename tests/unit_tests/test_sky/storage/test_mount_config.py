"""Tests for MountConfig and Storage config field functionality."""
from unittest import mock

import pytest

from sky.data import mounting_utils
from sky.data import storage


class TestMountConfig:
    """Tests for the MountConfig dataclass."""

    def test_mount_config_defaults(self):
        """Test MountConfig default values."""
        config = storage.MountConfig()
        assert config.readonly is False
        assert config.sequential_upload is None  # None = use global config

    @pytest.mark.parametrize('input_dict,expected_readonly,expected_seq', [
        (None, False, None),
        ({}, False, None),
        ({
            'readonly': True
        }, True, None),
        ({
            'sequential_upload': False
        }, False, False),
        ({
            'sequential_upload': True
        }, False, True),
        ({
            'readonly': True,
            'sequential_upload': False
        }, True, False),
        ({
            'readonly': True,
            'unknown_field': 'value'
        }, True, None),
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
        (None, False, None),
        ({}, False, None),
        ({
            'readonly': True
        }, True, None),
        ({
            'sequential_upload': False
        }, False, False),
        ({
            'sequential_upload': True
        }, False, True),
        ({
            'readonly': True,
            'sequential_upload': False
        }, True, False),
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

    def test_sequential_upload_none_not_serialized(self):
        """Test sequential_upload=None (default) is not serialized."""
        mount_config = storage.MountConfig(sequential_upload=None)
        storage_obj = storage.Storage(name='test-bucket',
                                      mount_config=mount_config)
        yaml_config = storage_obj.to_yaml_config()
        assert 'config' not in yaml_config

    @pytest.mark.parametrize(
        'ro,seq,expect_ro,expect_seq',
        [
            (True, None, True, None),  # readonly only, seq is default
            (False, False, None, False),  # seq=False, readonly is default
            (False, True, None, True),  # seq=True explicit
            (True, False, True, False),  # both non-default
            (True, True, True, True),  # readonly + seq=True explicit
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

    @pytest.mark.parametrize(
        'global_seq,cfg_seq,expect_seq',
        [
            # Global config = False (parallel), no per-bucket override
            (False, None, False),
            # Global config = True (sequential), no per-bucket override
            (True, None, True),
            # Global config = False, per-bucket override to True
            (False, True, True),
            # Global config = True, per-bucket override to False
            (True, False, False),
            # Global config = False, per-bucket confirms False
            (False, False, False),
            # Global config = True, per-bucket confirms True
            (True, True, True),
        ])
    def test_mount_cached_cmd_sequential(self, global_seq, cfg_seq, expect_seq):
        """Test get_mount_cached_cmd sequential_upload with global config."""
        with mock.patch('sky.data.mounting_utils.skypilot_config.get_nested',
                        return_value=global_seq):
            mount_config = storage.MountConfig(
                sequential_upload=cfg_seq) if cfg_seq is not None else (
                    storage.MountConfig() if cfg_seq is None else None)
            # Handle the case where we want to test with None mount_config
            if cfg_seq is None:
                mount_config = storage.MountConfig()  # seq=None by default
            cmd = self._get_cmd(mount_config)
            assert ('--transfers 1' in cmd) == expect_seq

    @pytest.mark.parametrize('ro,expect_ro', [
        (False, False),
        (True, True),
    ])
    def test_mount_cached_cmd_readonly(self, ro, expect_ro):
        """Test get_mount_cached_cmd readonly flag."""
        with mock.patch('sky.data.mounting_utils.skypilot_config.get_nested',
                        return_value=False):
            mount_config = storage.MountConfig(readonly=ro)
            cmd = self._get_cmd(mount_config)
            assert ('--read-only' in cmd) == expect_ro

    def test_mount_cached_cmd_none_config(self):
        """Test get_mount_cached_cmd with None mount_config uses global."""
        # With global config = True (sequential)
        with mock.patch('sky.data.mounting_utils.skypilot_config.get_nested',
                        return_value=True):
            cmd = self._get_cmd(None)
            assert '--transfers 1' in cmd
            assert '--read-only' not in cmd

        # With global config = False (parallel)
        with mock.patch('sky.data.mounting_utils.skypilot_config.get_nested',
                        return_value=False):
            cmd = self._get_cmd(None)
            assert '--transfers 1' not in cmd
            assert '--read-only' not in cmd


class TestMountCmd:
    """Tests for mount commands (MOUNT mode) with mount config."""

    @pytest.mark.parametrize('ro,expect_flag', [(False, False), (True, True)])
    def test_s3_mount_cmd(self, ro, expect_flag):
        """Test get_s3_mount_cmd with readonly config."""
        cfg = storage.MountConfig(readonly=ro) if ro else None
        cmd = mounting_utils.get_s3_mount_cmd(bucket_name='my-bucket',
                                              mount_path='/mnt/data',
                                              _bucket_sub_path=None,
                                              mount_config=cfg)
        has_flag = '--read-only' in cmd or '-o ro' in cmd
        assert has_flag == expect_flag

    @pytest.mark.parametrize('ro,expect_flag', [(False, False), (True, True)])
    def test_gcs_mount_cmd(self, ro, expect_flag):
        """Test get_gcs_mount_cmd with readonly config."""
        cfg = storage.MountConfig(readonly=ro) if ro else None
        cmd = mounting_utils.get_gcs_mount_cmd(bucket_name='my-bucket',
                                               mount_path='/mnt/data',
                                               _bucket_sub_path=None,
                                               mount_config=cfg)
        assert ('-o ro' in cmd) == expect_flag

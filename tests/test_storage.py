import tempfile
import time

import pytest

from sky import exceptions
from sky.data import mounting_utils
from sky.data import storage as storage_lib


class TestStorageSpecLocalSource:
    """Tests for local sources"""

    def test_nonexist_local_source(self):
        storage_obj = storage_lib.Storage(
            name='test', source=f'/tmp/test-{int(time.time())}')
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_obj.construct()

        assert 'Local source path does not exist' in str(e)

    def test_source_trailing_slashes(self):
        storage_obj = storage_lib.Storage(name='test', source='/bin/')
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_obj.construct()
        assert 'Storage source paths cannot end with a slash' in str(e)

    def test_source_single_file(self):
        with tempfile.NamedTemporaryFile() as f:
            storage_obj = storage_lib.Storage(name='test', source=f.name)
            with pytest.raises(exceptions.StorageSourceError) as e:
                storage_obj.construct()
            assert 'Storage source path cannot be a file' in str(e)

    def test_source_multifile_conflict(self):
        storage_obj = storage_lib.Storage(
            name='test', source=['/myfile.txt', '/a/myfile.txt'])
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_obj.construct()
        assert 'Cannot have multiple files or directories' in str(e)


class TestStorageSpecValidation:
    """Storage specification validation tests"""

    # These tests do not create any buckets and can be run offline
    def test_source_and_name(self):
        """Tests when both name and source are specified"""
        storage_obj = storage_lib.Storage(name='test', source='/bin')
        storage_obj.construct()

        # When source is bucket URL and name is specified - invalid spec
        storage_obj = storage_lib.Storage(name='test',
                                          source='s3://tcga-2-open')
        with pytest.raises(exceptions.StorageSpecError) as e:
            storage_obj.construct()

        assert 'Storage name should not be specified if the source is a ' \
               'remote URI.' in str(e)

    def test_source_and_noname(self):
        """Tests when only source is specified"""
        # When source is local, name must be specified
        storage_obj = storage_lib.Storage(source='/bin')
        with pytest.raises(exceptions.StorageNameError) as e:
            storage_obj.construct()

        assert 'Storage name must be specified if the source is local' in str(e)

        # When source is bucket URL and name is not specified - valid spec
        # Cannot run this test because it requires AWS credentials to initialize
        # bucket.
        # storage_lib.Storage(source='s3://tcga-2-open')

    def test_name_and_nosource(self):
        """Tests when only name is specified"""
        # When mode is COPY and the storage object doesn't exist - error out
        storage_obj = storage_lib.Storage(name='sky-test-bucket',
                                          mode=storage_lib.StorageMode.COPY)
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_obj.construct()

        assert 'source must be specified when using COPY mode' in str(e)

        # When mode is MOUNT - valid spec (e.g., use for scratch space)
        storage_obj = storage_lib.Storage(name='sky-test-bucket',
                                          mode=storage_lib.StorageMode.MOUNT)
        storage_obj.construct()

    def test_noname_and_nosource(self):
        """Tests when neither name nor source is specified"""
        # Storage cannot be specified without name or source - invalid spec
        storage_obj = storage_lib.Storage()
        with pytest.raises(exceptions.StorageSpecError) as e:
            storage_obj.construct()

        assert 'Storage source or storage name must be specified' in str(e)

    def test_uri_in_name(self):
        """Tests when name is a URI.

        Other tests for invalid names require store-specific test cases, and
        are in test_smoke.py::TestStorageWithCredentials"""
        invalid_names = [
            's3://mybucket',
            'gs://mybucket',
            'r2://mybucket',
        ]

        for n in invalid_names:
            storage_obj = storage_lib.Storage(name=n)
            with pytest.raises(exceptions.StorageNameError) as e:
                storage_obj.construct()

            assert 'Prefix detected' in str(e)


class TestMountCachedConfig:
    """Tests for MountCachedConfig dataclass."""

    def test_default_config_has_defaults_only(self):
        config = storage_lib.MountCachedConfig()
        flags = config.to_rclone_flags()
        assert flags == '--vfs-cache-max-size 10G --vfs-write-back 1s'

    def test_transfers_with_auto_checkers(self):
        config = storage_lib.MountCachedConfig(transfers=8)
        flags = config.to_rclone_flags()
        assert '--transfers 8' in flags
        assert '--checkers 16' in flags

    def test_all_flags(self):
        config = storage_lib.MountCachedConfig(
            transfers=8,
            buffer_size='64M',
            vfs_cache_max_size='20G',
            vfs_cache_max_age='1h',
            vfs_read_ahead='128M',
            vfs_read_chunk_size='32M',
            vfs_read_chunk_streams=4,
            vfs_write_back='5s',
            read_only=True,
        )
        flags = config.to_rclone_flags()
        assert '--transfers 8' in flags
        assert '--checkers 16' in flags
        assert '--buffer-size 64M' in flags
        assert '--vfs-cache-max-size 20G' in flags
        assert '--vfs-cache-max-age 1h' in flags
        assert '--vfs-read-ahead 128M' in flags
        assert '--vfs-read-chunk-size 32M' in flags
        assert '--vfs-read-chunk-streams 4' in flags
        assert '--vfs-write-back 5s' in flags
        assert '--read-only' in flags

    def test_vfs_cache_max_size_default(self):
        config = storage_lib.MountCachedConfig()
        assert '--vfs-cache-max-size 10G' in config.to_rclone_flags()

    def test_vfs_cache_max_size_override(self):
        config = storage_lib.MountCachedConfig(vfs_cache_max_size='50G')
        flags = config.to_rclone_flags()
        assert '--vfs-cache-max-size 50G' in flags
        assert '10G' not in flags

    def test_vfs_write_back_default(self):
        config = storage_lib.MountCachedConfig()
        assert '--vfs-write-back 1s' in config.to_rclone_flags()

    def test_vfs_write_back_override(self):
        config = storage_lib.MountCachedConfig(vfs_write_back='5s')
        flags = config.to_rclone_flags()
        assert '--vfs-write-back 5s' in flags
        assert '--vfs-write-back 1s' not in flags

    def test_read_only_false_not_emitted(self):
        config = storage_lib.MountCachedConfig(read_only=False)
        assert '--read-only' not in config.to_rclone_flags()

    def test_round_trip_yaml(self):
        config = storage_lib.MountCachedConfig(transfers=8, read_only=True)
        yaml_dict = config.to_yaml_config()
        restored = storage_lib.MountCachedConfig.from_yaml_config(yaml_dict)
        assert restored.transfers == 8
        assert restored.read_only is True
        assert restored.buffer_size is None

    def test_round_trip_yaml_all_fields(self):
        config = storage_lib.MountCachedConfig(
            transfers=8,
            buffer_size='64M',
            vfs_cache_max_size='20G',
            vfs_cache_max_age='1h',
            vfs_read_ahead='128M',
            vfs_read_chunk_size='32M',
            vfs_read_chunk_streams=4,
            vfs_write_back='5s',
            read_only=True,
        )
        yaml_dict = config.to_yaml_config()
        restored = storage_lib.MountCachedConfig.from_yaml_config(yaml_dict)
        assert restored == config

    def test_to_yaml_config_omits_none(self):
        config = storage_lib.MountCachedConfig(transfers=4)
        yaml_dict = config.to_yaml_config()
        assert yaml_dict == {'transfers': 4}

    def test_from_yaml_config_empty(self):
        config = storage_lib.MountCachedConfig.from_yaml_config({})
        assert config == storage_lib.MountCachedConfig()


class TestStorageFromYamlWithMountCachedConfig:
    """Tests for Storage.from_yaml_config with mount_cached config."""

    def test_mode_validation_rejects_mount_mode(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'MOUNT',
            'config': {
                'mount_cached': {
                    'transfers': 8,
                },
            },
        }
        with pytest.raises(exceptions.StorageSpecError):
            storage_lib.Storage.from_yaml_config(yaml_config)

    def test_mode_validation_rejects_copy_mode(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'COPY',
            'config': {
                'mount_cached': {
                    'read_only': True,
                },
            },
        }
        with pytest.raises(exceptions.StorageSpecError):
            storage_lib.Storage.from_yaml_config(yaml_config)

    def test_mode_validation_accepts_mount_cached(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'MOUNT_CACHED',
            'config': {
                'mount_cached': {
                    'transfers': 8,
                    'read_only': True,
                },
            },
        }
        storage_obj = storage_lib.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_cached_config is not None
        assert storage_obj.mount_cached_config.transfers == 8
        assert storage_obj.mount_cached_config.read_only is True

    def test_no_config_field(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'MOUNT_CACHED',
        }
        storage_obj = storage_lib.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_cached_config is None

    def test_empty_mount_cached_config(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'MOUNT_CACHED',
            'config': {
                'mount_cached': {},
            },
        }
        storage_obj = storage_lib.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_cached_config is not None
        assert storage_obj.mount_cached_config == storage_lib.MountCachedConfig(
        )

    def test_config_without_mount_cached(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'MOUNT_CACHED',
            'config': {},
        }
        storage_obj = storage_lib.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mount_cached_config is None

    def test_mode_default_with_mount_cached_config_fails(self):
        """Default mode is MOUNT, so config.mount_cached should fail."""
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'config': {
                'mount_cached': {
                    'transfers': 4,
                },
            },
        }
        with pytest.raises(exceptions.StorageSpecError):
            storage_lib.Storage.from_yaml_config(yaml_config)

    def test_case_insensitive_mode(self):
        yaml_config = {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'mount_cached',
            'config': {
                'mount_cached': {
                    'transfers': 4,
                },
            },
        }
        storage_obj = storage_lib.Storage.from_yaml_config(yaml_config)
        assert storage_obj.mode == storage_lib.StorageMode.MOUNT_CACHED
        assert storage_obj.mount_cached_config.transfers == 4


class TestMountCachedSchemaValidation:
    """Tests for schema pattern validation of mount_cached config fields."""

    def _make_yaml_config(self, mount_cached_overrides):
        return {
            'name': 'test-bucket',
            'store': 's3',
            'mode': 'MOUNT_CACHED',
            'config': {
                'mount_cached': mount_cached_overrides,
            },
        }

    # --- rclone memory pattern: ^[0-9]+(b|k|m|g|t|p|B|K|M|G|T|P)?$ ---

    @pytest.mark.parametrize('field', [
        'buffer_size',
        'vfs_cache_max_size',
        'vfs_read_ahead',
        'vfs_read_chunk_size',
    ])
    @pytest.mark.parametrize('value', [
        '128M',
        '64K',
        '1G',
        '10T',
        '256B',
        '0',
        '100',
        '128m',
        '64k',
        '1g',
        '10t',
        '256b',
        '1024P',
        '1024p',
    ])
    def test_memory_fields_accept_valid(self, field, value):
        config = self._make_yaml_config({field: value})
        storage_obj = storage_lib.Storage.from_yaml_config(config)
        assert storage_obj.mount_cached_config is not None

    @pytest.mark.parametrize('field', [
        'buffer_size',
        'vfs_cache_max_size',
        'vfs_read_ahead',
        'vfs_read_chunk_size',
    ])
    @pytest.mark.parametrize('value', [
        '128MB',
        '64 K',
        '10GiB',
        '1.5G',
        'off',
        '',
        '10gb',
        '-1M',
    ])
    def test_memory_fields_reject_invalid(self, field, value):
        config = self._make_yaml_config({field: value})
        with pytest.raises(ValueError):
            storage_lib.Storage.from_yaml_config(config)

    # --- rclone duration pattern ---

    @pytest.mark.parametrize('field', [
        'vfs_cache_max_age',
        'vfs_write_back',
    ])
    @pytest.mark.parametrize('value', [
        '1s',
        '5m',
        '1h',
        '2d',
        '1w',
        '1M',
        '1y',
        '100ms',
        '1h30m',
        '2d12h',
        '1.5s',
        '0',
        '42',
        '3.14',
    ])
    def test_duration_fields_accept_valid(self, field, value):
        config = self._make_yaml_config({field: value})
        storage_obj = storage_lib.Storage.from_yaml_config(config)
        assert storage_obj.mount_cached_config is not None

    @pytest.mark.parametrize('field', [
        'vfs_cache_max_age',
        'vfs_write_back',
    ])
    @pytest.mark.parametrize('value', [
        '1 hour',
        'off',
        '',
        '5sec',
        '1min',
        '10x',
    ])
    def test_duration_fields_reject_invalid(self, field, value):
        config = self._make_yaml_config({field: value})
        with pytest.raises(ValueError):
            storage_lib.Storage.from_yaml_config(config)

    # --- integer constraints ---

    def test_transfers_minimum(self):
        config = self._make_yaml_config({'transfers': 0})
        with pytest.raises(ValueError):
            storage_lib.Storage.from_yaml_config(config)

    def test_vfs_read_chunk_streams_allows_zero(self):
        config = self._make_yaml_config({'vfs_read_chunk_streams': 0})
        storage_obj = storage_lib.Storage.from_yaml_config(config)
        assert storage_obj.mount_cached_config.vfs_read_chunk_streams == 0

    # --- additionalProperties: false ---

    def test_unknown_field_rejected(self):
        config = self._make_yaml_config({'bogus_flag': 'value'})
        with pytest.raises(ValueError):
            storage_lib.Storage.from_yaml_config(config)


class TestGetMountCachedCmdWithConfig:
    """Tests for mounting_utils.get_mount_cached_cmd with MountCachedConfig."""

    def test_default_no_config(self):
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data')
        assert '--vfs-cache-max-size 10G' in cmd
        assert '--vfs-write-back 1s' in cmd

    def test_with_transfers_override(self):
        config = storage_lib.MountCachedConfig(transfers=16)
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_cached_config=config)
        assert '--transfers 16' in cmd
        assert '--checkers 32' in cmd

    def test_vfs_cache_max_size_override(self):
        config = storage_lib.MountCachedConfig(vfs_cache_max_size='50G')
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_cached_config=config)
        assert '--vfs-cache-max-size 50G' in cmd
        assert cmd.count('--vfs-cache-max-size') == 1

    def test_read_only(self):
        config = storage_lib.MountCachedConfig(read_only=True)
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_cached_config=config)
        assert '--read-only' in cmd

    def test_all_flags_in_command(self):
        config = storage_lib.MountCachedConfig(
            transfers=8,
            buffer_size='64M',
            vfs_cache_max_size='20G',
            vfs_cache_max_age='1h',
            vfs_read_ahead='128M',
            vfs_read_chunk_size='32M',
            vfs_read_chunk_streams=4,
            vfs_write_back='5s',
            read_only=True,
        )
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[test]\ntype = s3',
            rclone_profile_name='test',
            bucket_name='my-bucket',
            mount_path='/mnt/data',
            mount_cached_config=config)
        # Hardcoded flags still present
        assert '--vfs-cache-mode full' in cmd
        assert '--vfs-fast-fingerprint' in cmd
        assert '--allow-other' in cmd
        # Per-bucket overrides
        assert '--transfers 8' in cmd
        assert '--checkers 16' in cmd
        assert '--buffer-size 64M' in cmd
        assert '--vfs-cache-max-size 20G' in cmd
        assert '--vfs-cache-max-age 1h' in cmd
        assert '--vfs-read-ahead 128M' in cmd
        assert '--vfs-read-chunk-size 32M' in cmd
        assert '--vfs-read-chunk-streams 4' in cmd
        assert '--vfs-write-back 5s' in cmd
        assert '--read-only' in cmd

    def test_command_structure(self):
        """Verify the command has the expected rclone mount structure."""
        cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config='[myprofile]\ntype = s3',
            rclone_profile_name='myprofile',
            bucket_name='my-bucket',
            mount_path='/mnt/data')
        assert 'rclone mount myprofile:my-bucket /mnt/data' in cmd
        assert '--daemon' in cmd
        assert '> /dev/null 2>&1' in cmd

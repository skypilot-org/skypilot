"""Unit tests for volume utilities."""

from unittest import mock

import pytest

from sky import exceptions
from sky import models
from sky.utils import status_lib
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

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_volume_found(self, mock_get_volume):
        """Test resolve with a valid volume."""
        mock_volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='us-central1',
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        mock_get_volume.return_value = {
            'name': 'test-volume',
            'handle': mock_volume_config,
            'status': status_lib.VolumeStatus.READY,
        }

        volume_mount = volume.VolumeMount.resolve('/data', 'test-volume')

        assert volume_mount.path == '/data'
        assert volume_mount.volume_name == 'test-volume'
        assert volume_mount.volume_config == mock_volume_config
        assert volume_mount.is_ephemeral is False
        assert volume_mount.sub_path is None
        mock_get_volume.assert_called_once_with('test-volume')

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_volume_with_sub_path(self, mock_get_volume):
        """Test resolve with a valid volume and sub_path."""
        mock_volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='us-central1',
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        mock_get_volume.return_value = {
            'name': 'test-volume',
            'handle': mock_volume_config,
            'status': status_lib.VolumeStatus.READY,
        }

        volume_mount = volume.VolumeMount.resolve('/data',
                                                  'test-volume',
                                                  sub_path='subdir/path')

        assert volume_mount.path == '/data'
        assert volume_mount.volume_name == 'test-volume'
        assert volume_mount.sub_path == 'subdir/path'
        assert volume_mount.is_ephemeral is False
        mock_get_volume.assert_called_once_with('test-volume')

    def test_resolve_rejects_invalid_characters_in_sub_path(self):
        """Test resolve rejects sub_path with invalid characters."""
        # Newline (YAML injection vector)
        with pytest.raises(ValueError, match='invalid characters'):
            volume.VolumeMount.resolve('/data',
                                       'vol',
                                       sub_path='models\nreadOnly: true')

        # Space
        with pytest.raises(ValueError, match='invalid characters'):
            volume.VolumeMount.resolve('/data', 'vol', sub_path='my dir/sub')

        # Absolute path (leading /)
        with pytest.raises(ValueError, match='invalid characters'):
            volume.VolumeMount.resolve('/data', 'vol', sub_path='/abs/path')

        # Empty string
        with pytest.raises(ValueError, match='invalid characters'):
            volume.VolumeMount.resolve('/data', 'vol', sub_path='')

    def test_resolve_rejects_directory_traversal_in_sub_path(self):
        """Test resolve rejects sub_path with directory traversal (..)."""
        with pytest.raises(ValueError, match='directory traversal'):
            volume.VolumeMount.resolve('/data', 'vol', sub_path='../etc')

        with pytest.raises(ValueError, match='directory traversal'):
            volume.VolumeMount.resolve('/data', 'vol', sub_path='a/../../b')

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_allows_dot_prefixed_names_in_sub_path(
            self, mock_get_volume):
        """Test resolve allows names like ..foo or .hidden (not traversal)."""
        mock_volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='us-central1',
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        mock_get_volume.return_value = {
            'name': 'test-volume',
            'handle': mock_volume_config,
            'status': status_lib.VolumeStatus.READY,
        }

        # ..foo is a valid directory name, not traversal
        vm = volume.VolumeMount.resolve('/data',
                                        'test-volume',
                                        sub_path='..foo/bar')
        assert vm.sub_path == '..foo/bar'

        # .hidden is a valid directory name
        vm = volume.VolumeMount.resolve('/data',
                                        'test-volume',
                                        sub_path='.hidden/dir')
        assert vm.sub_path == '.hidden/dir'

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_volume_not_found(self, mock_get_volume):
        """Test resolve with a non-existent volume."""
        mock_get_volume.return_value = None

        with pytest.raises(exceptions.VolumeNotFoundError) as exc_info:
            volume.VolumeMount.resolve('/data', 'non-existent-volume')

        assert 'non-existent-volume' in str(exc_info.value)
        assert 'not found' in str(exc_info.value)

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_volume_not_ready_without_error_message(
            self, mock_get_volume):
        """Test resolve with a volume that is not ready (no error message)."""
        mock_get_volume.return_value = {
            'name': 'test-volume',
            'handle': None,
            'status': status_lib.VolumeStatus.NOT_READY,
        }

        with pytest.raises(exceptions.VolumeNotReadyError) as exc_info:
            volume.VolumeMount.resolve('/data', 'test-volume')

        assert 'test-volume' in str(exc_info.value)
        assert 'not ready' in str(exc_info.value)
        assert 'Error:' not in str(exc_info.value)

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_volume_not_ready_with_error_message(self, mock_get_volume):
        """Test resolve with a volume that is not ready (with error message)."""
        mock_get_volume.return_value = {
            'name': 'test-volume',
            'handle': None,
            'status': status_lib.VolumeStatus.NOT_READY,
            'error_message': 'Storage quota exceeded',
        }

        with pytest.raises(exceptions.VolumeNotReadyError) as exc_info:
            volume.VolumeMount.resolve('/data', 'test-volume')

        assert 'test-volume' in str(exc_info.value)
        assert 'not ready' in str(exc_info.value)
        assert 'Error: Storage quota exceeded' in str(exc_info.value)

    def test_to_yaml_config_with_sub_path(self):
        """Test to_yaml_config includes sub_path when set."""
        volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        vm = volume.VolumeMount('/data',
                                'test-volume',
                                volume_config,
                                sub_path='my/sub')
        yaml_config = vm.to_yaml_config()

        assert yaml_config['sub_path'] == 'my/sub'
        assert yaml_config['path'] == '/data'
        assert yaml_config['volume_name'] == 'test-volume'

    def test_to_yaml_config_without_sub_path(self):
        """Test to_yaml_config omits sub_path when not set."""
        volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        vm = volume.VolumeMount('/data', 'test-volume', volume_config)
        yaml_config = vm.to_yaml_config()

        assert 'sub_path' not in yaml_config

    def test_from_yaml_config_with_sub_path(self):
        """Test from_yaml_config deserializes sub_path."""
        volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        config = {
            'path': '/data',
            'volume_name': 'test-volume',
            'is_ephemeral': False,
            'sub_path': 'models/v1',
            'volume_config': volume_config.model_dump(),
        }
        vm = volume.VolumeMount.from_yaml_config(config)

        assert vm.path == '/data'
        assert vm.volume_name == 'test-volume'
        assert vm.sub_path == 'models/v1'
        assert vm.is_ephemeral is False

    def test_from_yaml_config_without_sub_path(self):
        """Test from_yaml_config works without sub_path."""
        volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        config = {
            'path': '/data',
            'volume_name': 'test-volume',
            'is_ephemeral': False,
            'volume_config': volume_config.model_dump(),
        }
        vm = volume.VolumeMount.from_yaml_config(config)

        assert vm.sub_path is None

    def test_roundtrip_sub_path(self):
        """Test sub_path survives to_yaml_config -> from_yaml_config."""
        volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region=None,
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        original = volume.VolumeMount('/data',
                                      'test-volume',
                                      volume_config,
                                      sub_path='a/b/c')
        restored = volume.VolumeMount.from_yaml_config(
            original.to_yaml_config())

        assert restored.path == original.path
        assert restored.volume_name == original.volume_name
        assert restored.sub_path == original.sub_path
        assert restored.is_ephemeral == original.is_ephemeral

    @mock.patch('sky.global_user_state.get_volume_by_name')
    def test_resolve_volume_status_none(self, mock_get_volume):
        """Test resolve with a volume where status is None."""
        mock_volume_config = models.VolumeConfig(
            name='test-volume',
            type='k8s-pvc',
            cloud='kubernetes',
            region='us-central1',
            zone=None,
            name_on_cloud='pvc-12345',
            size='10',
            config={},
            labels=None,
        )
        mock_get_volume.return_value = {
            'name': 'test-volume',
            'handle': mock_volume_config,
            'status': None,
        }

        volume_mount = volume.VolumeMount.resolve('/data', 'test-volume')

        assert volume_mount.path == '/data'
        assert volume_mount.volume_name == 'test-volume'
        assert volume_mount.volume_config == mock_volume_config
        assert volume_mount.is_ephemeral is False


PVC_TYPE = 'k8s-pvc'
HOSTPATH_TYPE = 'k8s-hostpath'


def _pvc(name, path, pvc_name):
    return volume.VolumeInfo(name=name,
                             path=path,
                             volume_name_on_cloud=pvc_name,
                             volume_type=PVC_TYPE)


def _hostpath(name, path, host_path):
    return volume.VolumeInfo(name=name,
                             path=path,
                             host_path=host_path,
                             volume_type=HOSTPATH_TYPE)


def _ephemeral(name, path):
    return volume.VolumeInfo(name=name, path=path, volume_type=PVC_TYPE)


class TestVolumeMountConflictChecker:
    """Tests for VolumeMountConflictChecker."""

    # -- Check 1: Mount path uniqueness --

    def test_mount_path_conflict_persistent_vs_automount(self):
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('my-vol', '/mnt/data', 'my-pvc'),
                      'task YAML volumes', 'volume my-vol')
        with pytest.raises(ValueError, match='mount path conflict'):
            checker.check(_pvc('auto-mount-shared', '/mnt/data', 'shared-pvc'),
                          'auto_mounts config', 'auto-mount volume shared')

    def test_mount_path_conflict_ephemeral_vs_persistent(self):
        checker = volume.VolumeMountConflictChecker()
        checker.check(_ephemeral('eph-123', '/mnt/data'),
                      'task YAML volumes (ephemeral)', 'ephemeral volume')
        with pytest.raises(ValueError, match='mount path conflict'):
            checker.check(_pvc('my-vol', '/mnt/data', 'my-pvc'),
                          'task YAML volumes', 'volume my-vol')

    def test_mount_path_conflict_ephemeral_vs_automount(self):
        checker = volume.VolumeMountConflictChecker()
        checker.check(_ephemeral('eph-123', '/mnt/data'),
                      'task YAML volumes (ephemeral)', 'ephemeral volume')
        with pytest.raises(ValueError, match='mount path conflict'):
            checker.check(_pvc('auto-mount-shared', '/mnt/data', 'shared-pvc'),
                          'auto_mounts config', 'auto-mount volume shared')

    # -- Check 2: Volume name consistency --

    def test_name_conflict_different_pvc(self):
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('shared-name', '/mnt/a', 'pvc-a'),
                      'task YAML volumes', 'volume vol-a')
        with pytest.raises(ValueError, match='Volume name conflict'):
            checker.check(_pvc('shared-name', '/mnt/b', 'pvc-b'),
                          'auto_mounts config', 'auto-mount volume vol-b')

    def test_name_conflict_ephemeral_vs_persistent(self):
        """Ephemeral and persistent share a name but different PVCs."""
        checker = volume.VolumeMountConflictChecker()
        checker.check(_ephemeral('vol-x', '/mnt/a'),
                      'task YAML volumes (ephemeral)', 'ephemeral vol')
        with pytest.raises(ValueError, match='Volume name conflict'):
            checker.check(_pvc('vol-x', '/mnt/b', 'different-pvc'),
                          'task YAML volumes', 'volume vol-x')

    def test_name_conflict_ephemeral_vs_ephemeral(self):
        """Two ephemeral volumes with hash-colliding names."""
        checker = volume.VolumeMountConflictChecker()
        checker.check(_ephemeral('cluster-abc123', '/mnt/a'),
                      'task YAML volumes (ephemeral)', 'ephemeral vol 1')
        with pytest.raises(ValueError, match='Volume name conflict'):
            checker.check(_ephemeral('cluster-abc123', '/mnt/b'),
                          'task YAML volumes (ephemeral)', 'ephemeral vol 2')

    def test_name_same_pvc_ok(self):
        """Auto-mount multiple mount_paths: same name, same PVC."""
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('auto-mount-shared', '/mnt/a', 'shared-pvc'),
                      'auto_mounts config', 'auto-mount volume shared')
        checker.check(_pvc('auto-mount-shared', '/mnt/b', 'shared-pvc'),
                      'auto_mounts config', 'auto-mount volume shared')

    def test_name_same_hostpath_ok(self):
        """Same hostPath volume at different mount paths."""
        checker = volume.VolumeMountConflictChecker()
        checker.check(_hostpath('auto-mount-hv', '/mnt/a', '/data/shared'),
                      'auto_mounts config', 'auto-mount volume hv')
        checker.check(_hostpath('auto-mount-hv', '/mnt/b', '/data/shared'),
                      'auto_mounts config', 'auto-mount volume hv')

    # -- Check 3: Same PVC from different volume entries --

    def test_pvc_conflict_different_names(self):
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('vol-a', '/mnt/a', 'shared-pvc'),
                      'task YAML volumes', 'volume vol-a')
        with pytest.raises(ValueError, match='PVC conflict'):
            checker.check(_pvc('auto-mount-vol-b', '/mnt/b', 'shared-pvc'),
                          'auto_mounts config', 'auto-mount volume vol-b')

    def test_pvc_conflict_two_automounts(self):
        """Two different auto_mount entries referencing the same PVC."""
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('auto-mount-vol-a', '/mnt/a', 'shared-pvc'),
                      'auto_mounts config', 'auto-mount volume vol-a')
        with pytest.raises(ValueError, match='PVC conflict'):
            checker.check(_pvc('auto-mount-vol-b', '/mnt/b', 'shared-pvc'),
                          'auto_mounts config', 'auto-mount volume vol-b')

    # -- No conflict scenarios --

    def test_no_conflict_different_volumes_different_paths(self):
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('vol-a', '/mnt/a', 'pvc-a'), 'task YAML volumes',
                      'volume vol-a')
        checker.check(_pvc('auto-mount-vol-b', '/mnt/b', 'pvc-b'),
                      'auto_mounts config', 'auto-mount volume vol-b')

    def test_no_conflict_automount_multiple_paths(self):
        """One auto_mount volume at three different mount paths."""
        checker = volume.VolumeMountConflictChecker()
        for path in ['/mnt/a', '/mnt/b', '/mnt/c']:
            checker.check(_pvc('auto-mount-shared', path, 'shared-pvc'),
                          'auto_mounts config', 'auto-mount volume shared')

    def test_no_conflict_mixed_types(self):
        """PVC, hostPath, and ephemeral at different paths."""
        checker = volume.VolumeMountConflictChecker()
        checker.check(_pvc('pvc-vol', '/mnt/pvc', 'my-pvc'),
                      'task YAML volumes', 'volume pvc-vol')
        checker.check(_hostpath('auto-mount-hv', '/mnt/host', '/data/host'),
                      'auto_mounts config', 'auto-mount volume hv')
        checker.check(_ephemeral('cluster-eph1', '/mnt/eph'),
                      'task YAML volumes (ephemeral)', 'ephemeral vol')

    # -- _get_vol_source_identity --

    def test_vol_source_identity_pvc(self):
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            PVC_TYPE, vol_name_on_cloud='my-pvc')
        assert identity == 'pvc:my-pvc'

    def test_vol_source_identity_hostpath(self):
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            HOSTPATH_TYPE, vol_host_path='/data/dir')
        assert identity == 'hostpath:/data/dir'

    def test_vol_source_identity_unknown(self):
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            None)
        assert identity is None

    def test_vol_source_identity_empty(self):
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            '')
        assert identity is None

    def test_vol_source_identity_pvc_none_cloud_name(self):
        """PVC type but vol_name_on_cloud is None should return None."""
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            PVC_TYPE, vol_name_on_cloud=None)
        assert identity is None

    def test_vol_source_identity_hostpath_none_path(self):
        """hostPath type but vol_host_path is None should return None."""
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            HOSTPATH_TYPE, vol_host_path=None)
        assert identity is None

    def test_vol_source_identity_runpod_returns_none(self):
        """RunPod network volume returns None (not supported yet)."""
        identity = volume.VolumeMountConflictChecker._get_vol_source_identity(
            'runpod-network-volume')
        assert identity is None

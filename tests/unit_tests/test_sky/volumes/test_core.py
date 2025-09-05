"""Unit tests for volume core functions."""

from datetime import datetime
from unittest import mock

import pytest

from sky import global_user_state
from sky import models
from sky import provision
from sky.utils import status_lib
from sky.volumes.server import core


class TestVolumeCore:
    """Test cases for volume core functions."""

    def test_volume_refresh_success(self, monkeypatch):
        """Test volume_refresh with successful status updates."""
        # Mock volume data
        mock_volumes = [{
            'name': 'test-volume-1',
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.IN_USE
        }, {
            'name': 'test-volume-2',
            'handle': mock.MagicMock(cloud='gcp', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.IN_USE
        }]

        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(
            return_value=([], []))  # No clusters using volume
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Mock global_user_state.get_volume_by_name
        mock_get_volume_by_name = mock.MagicMock(side_effect=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock global_user_state.update_volume_status
        mock_update_status = mock.MagicMock()
        monkeypatch.setattr(global_user_state, 'update_volume_status',
                            mock_update_status)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Call the function
        core.volume_refresh()

        # Verify calls
        mock_get_volumes.assert_called_once()
        assert mock_get_usedby.call_count == 2
        # Should be called for both volumes
        assert mock_update_status.call_count == 2
        expected_calls = [
            mock.call('test-volume-1', status=status_lib.VolumeStatus.READY),
            mock.call('test-volume-2', status=status_lib.VolumeStatus.READY)
        ]
        mock_update_status.assert_has_calls(expected_calls, any_order=True)

    def test_volume_refresh_inuse_success(self, monkeypatch):
        """Test volume_refresh with successful status updates."""
        # Mock volume data
        mock_volumes = [{
            'name': 'test-volume-1',
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.READY
        }, {
            'name': 'test-volume-2',
            'handle': mock.MagicMock(cloud='gcp', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.READY
        }]

        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(return_value=(['pod1', 'pod2'], [
            'cluster1', 'cluster2'
        ]))  # No clusters using volume
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Mock global_user_state.get_volume_by_name
        mock_get_volume_by_name = mock.MagicMock(side_effect=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock global_user_state.update_volume_status
        mock_update_status = mock.MagicMock()
        monkeypatch.setattr(global_user_state, 'update_volume_status',
                            mock_update_status)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Call the function
        core.volume_refresh()

        # Verify calls
        mock_get_volumes.assert_called_once()
        assert mock_get_usedby.call_count == 2
        # Should be called for both volumes
        assert mock_update_status.call_count == 2
        expected_calls = [
            mock.call('test-volume-1', status=status_lib.VolumeStatus.IN_USE),
            mock.call('test-volume-2', status=status_lib.VolumeStatus.IN_USE)
        ]
        mock_update_status.assert_has_calls(expected_calls, any_order=True)

    def test_volume_refresh_volume_without_handle(self, monkeypatch):
        """Test volume_refresh with volume that has no handle."""
        # Mock volume data without handle
        mock_volumes = [{'name': 'test-volume-no-handle', 'handle': None}]

        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Call the function - should not raise exception
        core.volume_refresh()

        # Verify get_volumes was called
        mock_get_volumes.assert_called_once()

    def test_volume_refresh_volume_not_found(self, monkeypatch):
        """Test volume_refresh when volume is not found during refresh."""
        # Mock volume data
        mock_volumes = [{
            'name': 'test-volume',
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.READY
        }]

        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(
            return_value=([], []))  # No clusters using volume
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Mock global_user_state.get_volume_by_name to return None
        mock_get_volume_by_name = mock.MagicMock(return_value=None)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Call the function - should not raise exception
        core.volume_refresh()

        # Verify calls
        mock_get_volumes.assert_called_once()
        mock_get_usedby.assert_called_once()

    def test_volume_list_success(self, monkeypatch):
        """Test volume_list with successful volume retrieval."""
        # Mock volume data
        mock_volumes = [{
            'name': 'test-volume-1',
            'launched_at': 1234567890,
            'user_hash': 'user123',
            'workspace': 'default',
            'last_attached_at': 1234567891,
            'last_use': 'sky volumes apply',
            'status': status_lib.VolumeStatus.READY,
            'handle': mock.MagicMock(type='k8s-pvc',
                                     cloud='aws',
                                     region='us-east-1',
                                     zone='us-east-1a',
                                     size='100Gi',
                                     config={'storage_class': 'gp2'},
                                     name_on_cloud='test-volume-1-abc123',
                                     spec=models.VolumeConfig)
        }, {
            'name': 'test-volume-2',
            'launched_at': 1234567892,
            'user_hash': 'user456',
            'workspace': 'default',
            'last_attached_at': None,
            'last_use': None,
            'status': None,
            'handle': mock.MagicMock(type='k8s-pvc',
                                     cloud='gcp',
                                     region='us-central1',
                                     zone=None,
                                     size='200Gi',
                                     config={},
                                     name_on_cloud='test-volume-2-def456',
                                     spec=models.VolumeConfig)
        }]

        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)
        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(
            return_value=(['pod1', 'pod2'], ['cluster1', 'cluster2']))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Call the function
        result = core.volume_list()

        # Verify result
        assert len(result) == 2

        # Check first volume
        vol1 = result[0]
        assert vol1['name'] == 'test-volume-1'
        assert vol1['launched_at'] == 1234567890
        assert vol1['user_hash'] == 'user123'
        assert vol1['workspace'] == 'default'
        assert vol1['last_attached_at'] == 1234567891
        assert vol1['last_use'] == 'sky volumes apply'
        assert vol1['status'] == 'READY'
        assert vol1['type'] == 'k8s-pvc'
        assert vol1['cloud'] == 'aws'
        assert vol1['region'] == 'us-east-1'
        assert vol1['zone'] == 'us-east-1a'
        assert vol1['size'] == '100Gi'
        assert vol1['config'] == {'storage_class': 'gp2'}
        assert vol1['name_on_cloud'] == 'test-volume-1-abc123'
        assert vol1['usedby_pods'] == ['pod1', 'pod2']
        assert vol1['usedby_clusters'] == ['cluster1', 'cluster2']
        # Check second volume
        vol2 = result[1]
        assert vol2['name'] == 'test-volume-2'
        assert vol2['status'] == ''  # None status becomes empty string
        assert vol2['zone'] is None
        assert vol2['usedby_pods'] == ['pod1', 'pod2']
        assert vol2['usedby_clusters'] == ['cluster1', 'cluster2']

    def test_volume_list_volume_without_handle(self, monkeypatch):
        """Test volume_list with volume that has no handle."""
        # Mock volume data without handle
        mock_volumes = [{
            'name': 'test-volume-no-handle',
            'launched_at': 1234567890,
            'user_hash': 'user123',
            'workspace': 'default',
            'last_attached_at': None,
            'last_use': None,
            'status': status_lib.VolumeStatus.READY,
            'handle': None
        }]

        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)

        # Call the function
        result = core.volume_list()

        # Verify result - should skip volume without handle
        assert len(result) == 0

    def test_volume_delete_success(self, monkeypatch):
        """Test volume_delete with successful deletion."""
        # Mock volume data
        mock_volume = {
            'name': 'test-volume',
            'status': status_lib.VolumeStatus.READY,
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig)
        }

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(return_value=mock_volume)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        mock_delete_volume = mock.MagicMock()
        monkeypatch.setattr(global_user_state, 'delete_volume',
                            mock_delete_volume)

        # Mock provision.delete_volume
        mock_provision_delete = mock.MagicMock()
        monkeypatch.setattr(provision, 'delete_volume', mock_provision_delete)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(return_value=([], []))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Call the function
        core.volume_delete(['test-volume'])

        # Verify calls
        mock_get_volume_by_name.assert_called_once_with('test-volume')
        mock_provision_delete.assert_called_once()
        mock_delete_volume.assert_called_once_with('test-volume')

    def test_volume_delete_volume_not_found(self, monkeypatch):
        """Test volume_delete with non-existent volume."""
        # Mock global_user_state to return None
        mock_get_volume_by_name = mock.MagicMock(return_value=None)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Call the function and expect ValueError
        with pytest.raises(ValueError, match='Volume test-volume not found.'):
            core.volume_delete(['test-volume'])

    def test_volume_delete_volume_in_use(self, monkeypatch):
        """Test volume_delete with volume that is in use."""
        # Mock volume data that is in use
        mock_volume = {
            'name': 'test-volume',
            'status': status_lib.VolumeStatus.IN_USE,
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig)
        }

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(return_value=mock_volume)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(
            return_value=(['pod1', 'pod2'], ['cluster1', 'cluster2']))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Call the function and expect ValueError
        with pytest.raises(ValueError, match='Volume test-volume is'):
            core.volume_delete(['test-volume'])

    def test_volume_delete_volume_in_use_by_pod(self, monkeypatch):
        """Test volume_delete with volume that is in use."""
        # Mock volume data that is in use
        mock_volume = {
            'name': 'test-volume',
            'status': status_lib.VolumeStatus.IN_USE,
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig)
        }

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(return_value=mock_volume)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(return_value=(['pod1', 'pod2'], []))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Call the function and expect ValueError
        with pytest.raises(ValueError, match='Volume test-volume is'):
            core.volume_delete(['test-volume'])

    def test_volume_delete_volume_without_handle(self, monkeypatch):
        """Test volume_delete with volume that has no handle."""
        # Mock volume data without handle
        mock_volume = {
            'name': 'test-volume',
            'status': status_lib.VolumeStatus.READY,
            'handle': None
        }

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(return_value=mock_volume)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(
            return_value=(['pod1', 'pod2'], ['cluster1', 'cluster2']))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Call the function and expect ValueError
        with pytest.raises(ValueError,
                           match='Volume test-volume has no handle.'):
            core.volume_delete(['test-volume'])

    def test_volume_delete_multiple_volumes(self, monkeypatch):
        """Test volume_delete with multiple volumes."""
        # Mock volume data for multiple volumes
        mock_volumes = [{
            'name': 'test-volume-1',
            'status': status_lib.VolumeStatus.READY,
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig)
        }, {
            'name': 'test-volume-2',
            'status': status_lib.VolumeStatus.READY,
            'handle': mock.MagicMock(cloud='gcp', spec=models.VolumeConfig)
        }]

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(side_effect=mock_volumes)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        mock_delete_volume = mock.MagicMock()
        monkeypatch.setattr(global_user_state, 'delete_volume',
                            mock_delete_volume)

        # Mock provision.delete_volume
        mock_provision_delete = mock.MagicMock()
        monkeypatch.setattr(provision, 'delete_volume', mock_provision_delete)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(return_value=([], []))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)

        # Call the function
        core.volume_delete(['test-volume-1', 'test-volume-2'])

        # Verify calls
        assert mock_get_volume_by_name.call_count == 2
        assert mock_provision_delete.call_count == 2
        assert mock_delete_volume.call_count == 2

    def test_volume_apply_success_new_volume(self, monkeypatch):
        """Test volume_apply with successful creation of new volume."""
        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.max_cluster_name_length.return_value = 63
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.utils.registry.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Mock common_utils.make_cluster_name_on_cloud
        mock_make_name = mock.MagicMock(return_value='test-volume-123')
        monkeypatch.setattr(
            'sky.volumes.server.core.common_utils.make_cluster_name_on_cloud',
            mock_make_name)

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(
            return_value=None)  # Volume doesn't exist
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        mock_add_volume = mock.MagicMock()
        monkeypatch.setattr(global_user_state, 'add_volume', mock_add_volume)

        # Mock provision.apply_volume
        mock_provision_apply = mock.MagicMock(return_value=mock.MagicMock())
        monkeypatch.setattr(provision, 'apply_volume', mock_provision_apply)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Mock uuid
        mock_uuid = mock.MagicMock()
        mock_uuid.uuid4.return_value = '1234567890abcdef'
        monkeypatch.setattr('sky.volumes.server.core.uuid', mock_uuid)

        # Call the function
        core.volume_apply(name='test-volume',
                          volume_type='k8s-pvc',
                          cloud='aws',
                          region='us-east-1',
                          zone='us-east-1a',
                          size='100Gi',
                          config={'storage_class': 'gp2'})

        # Verify calls
        mock_cloud_registry.from_str.assert_called_once_with('aws')
        mock_make_name.assert_called_once_with('test-volume', max_length=63)
        mock_get_volume_by_name.assert_called_once_with('test-volume')
        mock_provision_apply.assert_called_once()
        mock_add_volume.assert_called_once()

    def test_volume_apply_volume_already_exists(self, monkeypatch):
        """Test volume_apply with volume that already exists."""
        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.max_cluster_name_length.return_value = 63
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.utils.registry.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Mock global_user_state to return existing volume
        mock_get_volume_by_name = mock.MagicMock(
            return_value={'name': 'test-volume'})
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Mock uuid
        mock_uuid = mock.MagicMock()
        mock_uuid.uuid4.return_value = '1234567890abcdef'
        monkeypatch.setattr('sky.volumes.server.core.uuid', mock_uuid)

        # Call the function
        core.volume_apply(name='test-volume',
                          volume_type='k8s-pvc',
                          cloud='aws',
                          region='us-east-1',
                          zone='us-east-1a',
                          size='100Gi',
                          config={'storage_class': 'gp2'})

        # Verify that provision.apply_volume was not called
        # (since volume already exists)
        mock_get_volume_by_name.assert_called_once_with('test-volume')

    def test_volume_apply_with_none_values(self, monkeypatch):
        """Test volume_apply with None values for optional parameters."""
        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.max_cluster_name_length.return_value = 63
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.utils.registry.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Mock common_utils.make_cluster_name_on_cloud
        mock_make_name = mock.MagicMock(return_value='test-volume-123')
        monkeypatch.setattr(
            'sky.volumes.server.core.common_utils.make_cluster_name_on_cloud',
            mock_make_name)

        # Mock global_user_state
        mock_get_volume_by_name = mock.MagicMock(return_value=None)
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)

        mock_add_volume = mock.MagicMock()
        monkeypatch.setattr(global_user_state, 'add_volume', mock_add_volume)

        # Mock provision.apply_volume
        mock_provision_apply = mock.MagicMock(return_value=mock.MagicMock())
        monkeypatch.setattr(provision, 'apply_volume', mock_provision_apply)

        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)

        # Mock uuid
        mock_uuid = mock.MagicMock()
        mock_uuid.uuid4.return_value = '1234567890abcdef'
        monkeypatch.setattr('sky.volumes.server.core.uuid', mock_uuid)

        # Call the function with None values
        core.volume_apply(name='test-volume',
                          volume_type='k8s-pvc',
                          cloud='k8s',
                          region=None,
                          zone=None,
                          size=None,
                          config={})

        # Verify calls
        mock_provision_apply.assert_called_once()
        mock_add_volume.assert_called_once()

    def test_volume_lock_timeout(self, monkeypatch):
        """Test volume lock timeout handling."""

        # Define a real exception class for Timeout
        class FakeTimeout(Exception):
            pass

        # Mock filelock to raise Timeout
        def raise_timeout(*args, **kwargs):
            raise FakeTimeout("Timeout")

        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock.MagicMock(side_effect=raise_timeout))
        monkeypatch.setattr('sky.volumes.server.core.filelock.Timeout',
                            FakeTimeout)
        # Mock global_user_state
        mock_get_volumes = mock.MagicMock(return_value=[{
            'name': 'test-volume',
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.READY
        }])
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)
        # Mock provision.get_volume_usedby
        mock_get_usedby = mock.MagicMock(return_value=([], []))
        monkeypatch.setattr(provision, 'get_volume_usedby', mock_get_usedby)
        # Mock global_user_state.get_volume_by_name
        mock_get_volume_by_name = mock.MagicMock(
            return_value={
                'name': 'test-volume',
                'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig),
                'status': status_lib.VolumeStatus.READY
            })
        monkeypatch.setattr(global_user_state, 'get_volume_by_name',
                            mock_get_volume_by_name)
        # Call a function that uses volume lock and expect RuntimeError
        with pytest.raises(RuntimeError):
            core.volume_refresh()

    def test_volume_lock_success(self, monkeypatch):
        """Test volume lock successful acquisition."""
        # Mock filelock
        mock_filelock = mock.MagicMock()
        monkeypatch.setattr('sky.volumes.server.core.filelock.FileLock',
                            mock_filelock)
        # Provide a dummy volume with a handle so the lock is used
        dummy_volume = {
            'name': 'test-volume',
            'handle': mock.MagicMock(cloud='aws', spec=models.VolumeConfig),
            'status': status_lib.VolumeStatus.READY
        }
        mock_get_volumes = mock.MagicMock(return_value=[dummy_volume])
        monkeypatch.setattr(global_user_state, 'get_volumes', mock_get_volumes)
        # Mock provision.get_volume_usedby to return non-empty so status is not updated
        monkeypatch.setattr(provision, 'get_volume_usedby',
                            mock.MagicMock(return_value=(['in-use'], [])))
        # Call the function - should not raise exception
        core.volume_refresh()
        # Verify filelock was used as a context manager
        mock_filelock.assert_called()
        instance = mock_filelock.return_value
        assert instance.__enter__.called

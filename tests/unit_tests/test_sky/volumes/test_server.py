"""Unit tests for volume server API."""

from unittest import mock

import fastapi
from fastapi.testclient import TestClient
import pytest

from sky import clouds
from sky.server.requests import executor
from sky.server.requests import payloads
from sky.server.requests import requests as requests_lib
from sky.utils import volume
from sky.volumes.server import server


class TestVolumeServer:
    """Test cases for volume server API endpoints."""

    def test_volume_list_success(self, monkeypatch):
        """Test volume_list endpoint with successful request."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Make request
            response = client.get('/volumes')

            # Verify response
            assert response.status_code == 200

            # Verify executor was called correctly
            mock_schedule.assert_called_once_with(
                request_id='test-request-id',
                request_name='volume_list',
                request_body=payloads.RequestBody(),
                func=server.core.volume_list,
                schedule_type=requests_lib.ScheduleType.SHORT,
            )

    def test_volume_delete_success(self, monkeypatch):
        """Test volume_delete endpoint with successful request."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data
            delete_body = {'names': ['test-volume-1', 'test-volume-2']}

            # Make request
            response = client.post('/volumes/delete', json=delete_body)

            # Verify response
            assert response.status_code == 200

            # Verify executor was called correctly
            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args
            assert call_args[1]['request_id'] == 'test-request-id'
            assert call_args[1]['request_name'] == 'volume_delete'
            assert call_args[1]['func'] == server.core.volume_delete
            assert call_args[1][
                'schedule_type'] == requests_lib.ScheduleType.LONG
            assert isinstance(call_args[1]['request_body'],
                              payloads.VolumeDeleteBody)

    def test_volume_apply_success_pvc(self, monkeypatch):
        """Test volume_apply endpoint with successful PVC volume creation."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.is_same_cloud.return_value = True
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data for PVC volume
            apply_body = {
                'name': 'test-pvc-volume',
                'volume_type': volume.VolumeType.PVC.value,
                'cloud': 'k8s',
                'region': 'context-name',
                'size': '100Gi',
                'config': {
                    'access_mode':
                        volume.VolumeAccessMode.READ_WRITE_ONCE.value,
                    'storage_class': 'gp2'
                }
            }

            # Make request
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 200

            # Verify executor was called correctly
            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args
            assert call_args[1]['request_id'] == 'test-request-id'
            assert call_args[1]['request_name'] == 'volume_apply'
            assert call_args[1]['func'] == server.core.volume_apply
            assert call_args[1][
                'schedule_type'] == requests_lib.ScheduleType.LONG
            assert isinstance(call_args[1]['request_body'],
                              payloads.VolumeApplyBody)

    def test_volume_apply_success_pvc_default_access_mode(self, monkeypatch):
        """Test volume_apply endpoint with PVC volume using default access mode."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.is_same_cloud.return_value = True
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data for PVC volume without access_mode
            apply_body = {
                'name': 'test-pvc-volume',
                'volume_type': volume.VolumeType.PVC.value,
                'cloud': 'k8s',
                'region': 'context-name',
                'size': '100Gi',
                'config': {
                    'storage_class': 'gp2'
                }
            }

            # Make request
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 200

            # Verify executor was called correctly
            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args
            assert call_args[1]['request_id'] == 'test-request-id'
            assert call_args[1]['request_name'] == 'volume_apply'
            assert call_args[1]['func'] == server.core.volume_apply
            assert call_args[1][
                'schedule_type'] == requests_lib.ScheduleType.LONG

    def test_volume_apply_success_pvc_none_config(self, monkeypatch):
        """Test volume_apply endpoint with PVC volume and None config."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.is_same_cloud.return_value = True
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data for PVC volume with None config
            apply_body = {
                'name': 'test-pvc-volume',
                'volume_type': volume.VolumeType.PVC.value,
                'cloud': 'k8s',
                'region': 'context-name',
                'size': '100Gi',
                'config': None
            }

            # Make request
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 200

            # Verify executor was called correctly
            mock_schedule.assert_called_once()

    def test_volume_apply_invalid_volume_type(self, monkeypatch):
        """Test volume_apply endpoint with invalid volume type."""
        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data with invalid volume type
            apply_body = {
                'name': 'test-volume',
                'volume_type': 'invalid-type',
                'cloud': 'k8s',
                'region': 'context-name',
                'size': '100Gi',
                'config': {}
            }

            # Make request and expect error
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 400
            assert 'Invalid volume type: invalid-type' in response.json(
            )['detail']

    def test_volume_apply_invalid_cloud(self, monkeypatch):
        """Test volume_apply endpoint with invalid cloud."""
        # Mock cloud registry to return None
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = None
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data with invalid cloud
            apply_body = {
                'name': 'test-volume',
                'volume_type': volume.VolumeType.PVC.value,
                'cloud': 'invalid-cloud',
                'region': 'context-name',
                'size': '100Gi',
                'config': {}
            }

            # Make request and expect error
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 400
            assert 'Invalid cloud: invalid-cloud' in response.json()['detail']

    def test_volume_apply_pvc_non_kubernetes_cloud(self, monkeypatch):
        """Test volume_apply endpoint with PVC volume on non-Kubernetes cloud."""
        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.is_same_cloud.return_value = False  # Not Kubernetes
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data for PVC volume on non-Kubernetes cloud
            apply_body = {
                'name': 'test-pvc-volume',
                'volume_type': volume.VolumeType.PVC.value,
                'cloud': 'aws',  # Non-Kubernetes cloud
                'region': 'context-name',
                'size': '100Gi',
                'config': {}
            }

            # Make request and expect error
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 400
            assert 'PVC storage is only supported on Kubernetes' in response.json(
            )['detail']

    def test_volume_apply_pvc_invalid_access_mode(self, monkeypatch):
        """Test volume_apply endpoint with PVC volume and invalid access mode."""
        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud.is_same_cloud.return_value = True  # Is Kubernetes
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data for PVC volume with invalid access mode
            apply_body = {
                'name': 'test-pvc-volume',
                'volume_type': volume.VolumeType.PVC.value,
                'cloud': 'k8s',
                'region': 'context-name',
                'size': '100Gi',
                'config': {
                    'access_mode': 'invalid-access-mode'
                }
            }

            # Make request and expect error
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 400
            assert 'Invalid access mode: invalid-access-mode' in response.json(
            )['detail']

    def test_volume_apply_non_pvc_volume_type(self, monkeypatch):
        """Test volume_apply endpoint with non-PVC volume type."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Mock cloud registry
        mock_cloud = mock.MagicMock()
        mock_cloud_registry = mock.MagicMock()
        mock_cloud_registry.from_str.return_value = mock_cloud
        monkeypatch.setattr('sky.volumes.server.server.sky.CLOUD_REGISTRY',
                            mock_cloud_registry)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data for non-PVC volume type (assuming there's another type)
            # Note: This test assumes there are other volume types besides PVC
            # If PVC is the only type, this test might need adjustment
            apply_body = {
                'name': 'test-volume',
                'volume_type': 'other-type',  # Non-PVC type
                'cloud': 'k8s',
                'region': 'context-name',
                'size': '100Gi',
                'config': {}
            }

            # Make request
            response = client.post('/volumes/apply', json=apply_body)

            # Verify response
            assert response.status_code == 400
            assert 'Invalid volume type' in response.json()['detail']

    def test_volume_delete_empty_volume_names(self, monkeypatch):
        """Test volume_delete endpoint with empty volume names list."""
        # Mock executor.schedule_request
        mock_schedule = mock.MagicMock()
        monkeypatch.setattr(executor, 'schedule_request', mock_schedule)

        # Create test client
        app = fastapi.FastAPI()
        app.include_router(server.router, prefix='/volumes')
        client = TestClient(app)

        # Mock request state
        with mock.patch.object(fastapi.Request, 'state') as mock_state:
            mock_state.request_id = 'test-request-id'

            # Test data with empty volume names
            delete_body = {'names': []}

            # Make request
            response = client.post('/volumes/delete', json=delete_body)

            # Verify response
            assert response.status_code == 200

            # Verify executor was called correctly
            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args
            assert call_args[1]['request_id'] == 'test-request-id'
            assert call_args[1]['request_name'] == 'volume_delete'
            assert call_args[1]['func'] == server.core.volume_delete
            assert call_args[1][
                'schedule_type'] == requests_lib.ScheduleType.LONG

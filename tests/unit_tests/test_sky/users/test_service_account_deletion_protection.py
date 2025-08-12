"""Tests for service account deletion protection when active resources exist."""

import unittest.mock as mock

import fastapi
import pytest

from sky import global_user_state
from sky.server.requests import payloads
from sky.users import server as users_server
from sky.users.permission import permission_service


class TestServiceAccountDeletionProtection:
    """Test cases for service account deletion protection."""

    @pytest.fixture
    def mock_auth_user(self):
        """Mock authenticated user."""
        user = mock.Mock()
        user.id = 'user123'
        user.name = 'testuser'
        return user

    @pytest.fixture
    def mock_request(self, mock_auth_user):
        """Mock FastAPI request with authenticated user."""
        request = mock.Mock()
        request.state.auth_user = mock_auth_user
        return request

    @pytest.fixture
    def sample_service_account_token(self):
        """Sample service account token data."""
        return {
            'token_id': 'token123',
            'token_name': 'test-service-account',
            'creator_user_hash': 'user123',
            'service_account_user_id': 'sa-abc123def456',
            'created_at': 1234567890,
            'expires_at': 1999999999
        }

    @pytest.mark.asyncio
    @mock.patch('sky.users.server.global_user_state.get_service_account_token')
    @mock.patch('sky.users.server._delete_user')
    @mock.patch(
        'sky.users.server.global_user_state.delete_service_account_token')
    async def test_delete_service_account_token_success_no_active_resources(
            self, mock_delete_token, mock_delete_user, mock_get_token,
            mock_request, sample_service_account_token):
        """Test successful service account token deletion when no active resources exist."""
        # Setup mocks
        mock_get_token.return_value = sample_service_account_token
        mock_delete_user.return_value = None  # No exception means no active resources
        mock_delete_token.return_value = True

        # Create delete request
        delete_body = payloads.ServiceAccountTokenDeleteBody(
            token_id='token123')

        # Execute deletion
        result = await users_server.delete_service_account_token(
            mock_request, delete_body)

        # Verify deletion flow
        mock_get_token.assert_called_once_with('token123')
        mock_delete_user.assert_called_once_with('sa-abc123def456')

        assert result == {'message': 'Token deleted successfully'}

    @pytest.mark.asyncio
    @mock.patch('sky.users.server.global_user_state.get_service_account_token')
    @mock.patch('sky.users.server.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    async def test_delete_service_account_token_blocked_by_active_clusters(
            self, mock_check_resources, mock_get_user, mock_get_token,
            mock_request, sample_service_account_token):
        """Test service account token deletion blocked by active clusters."""
        # Setup mocks
        mock_get_token.return_value = sample_service_account_token

        # Mock user exists
        mock_user = mock.Mock()
        mock_user.id = 'sa-abc123def456'
        mock_user.name = 'test-service-account'
        mock_get_user.return_value = mock_user

        # Simulate active clusters blocking deletion
        mock_check_resources.side_effect = ValueError(
            "Cannot delete user 'sa-abc123def456' because it has "
            "2 active cluster(s): test-cluster-1, test-cluster-2. "
            "Please terminate these resources first.")

        # Create delete request
        delete_body = payloads.ServiceAccountTokenDeleteBody(
            token_id='token123')

        # Execute deletion and expect it to fail
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await users_server.delete_service_account_token(
                mock_request, delete_body)

        # Verify error details
        assert exc_info.value.status_code == 400
        assert "active cluster(s)" in exc_info.value.detail
        assert "test-cluster-1" in exc_info.value.detail
        assert "test-cluster-2" in exc_info.value.detail
        assert "Please terminate these resources first" in exc_info.value.detail

        # Verify the resource check was called
        mock_check_resources.assert_called_once_with([('sa-abc123def456',
                                                       'delete')])

    @pytest.mark.asyncio
    @mock.patch('sky.users.server.global_user_state.get_service_account_token')
    @mock.patch('sky.users.server.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    async def test_delete_service_account_token_blocked_by_active_jobs(
            self, mock_check_resources, mock_get_user, mock_get_token,
            mock_request, sample_service_account_token):
        """Test service account token deletion blocked by active managed jobs."""
        # Setup mocks
        mock_get_token.return_value = sample_service_account_token

        # Mock user exists
        mock_user = mock.Mock()
        mock_user.id = 'sa-abc123def456'
        mock_user.name = 'test-service-account'
        mock_get_user.return_value = mock_user

        # Simulate active managed jobs blocking deletion
        mock_check_resources.side_effect = ValueError(
            "Cannot delete user 'sa-abc123def456' because it has "
            "1 active managed job(s): job-456. "
            "Please terminate these resources first.")

        # Create delete request
        delete_body = payloads.ServiceAccountTokenDeleteBody(
            token_id='token123')

        # Execute deletion and expect it to fail
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await users_server.delete_service_account_token(
                mock_request, delete_body)

        # Verify error details
        assert exc_info.value.status_code == 400
        assert "active managed job(s)" in exc_info.value.detail
        assert "job-456" in exc_info.value.detail
        assert "Please terminate these resources first" in exc_info.value.detail

    @pytest.mark.asyncio
    @mock.patch('sky.users.server.global_user_state.get_service_account_token')
    @mock.patch('sky.users.server.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    async def test_delete_service_account_token_blocked_by_mixed_resources(
            self, mock_check_resources, mock_get_user, mock_get_token,
            mock_request, sample_service_account_token):
        """Test service account token deletion blocked by both clusters and jobs."""
        # Setup mocks
        mock_get_token.return_value = sample_service_account_token

        # Mock user exists
        mock_user = mock.Mock()
        mock_user.id = 'sa-abc123def456'
        mock_user.name = 'test-service-account'
        mock_get_user.return_value = mock_user

        # Simulate both clusters and jobs blocking deletion
        mock_check_resources.side_effect = ValueError(
            "Cannot delete user 'sa-abc123def456' because it has "
            "1 active cluster(s): production-cluster and "
            "2 active managed job(s): training-job, inference-job. "
            "Please terminate these resources first.")

        # Create delete request
        delete_body = payloads.ServiceAccountTokenDeleteBody(
            token_id='token123')

        # Execute deletion and expect it to fail
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await users_server.delete_service_account_token(
                mock_request, delete_body)

        # Verify error details
        assert exc_info.value.status_code == 400
        assert "active cluster(s)" in exc_info.value.detail
        assert "active managed job(s)" in exc_info.value.detail
        assert "production-cluster" in exc_info.value.detail
        assert "training-job" in exc_info.value.detail
        assert "inference-job" in exc_info.value.detail

    @pytest.mark.asyncio
    @mock.patch('sky.users.server.global_user_state.get_service_account_token')
    async def test_delete_service_account_token_not_found(
            self, mock_get_token, mock_request):
        """Test service account token deletion when token doesn't exist."""
        # Setup mocks - token not found
        mock_get_token.return_value = None

        # Create delete request
        delete_body = payloads.ServiceAccountTokenDeleteBody(
            token_id='nonexistent')

        # Execute deletion and expect not found error
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await users_server.delete_service_account_token(
                mock_request, delete_body)

        # Verify not found error
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == 'Token not found'

    @pytest.mark.asyncio
    async def test_delete_service_account_token_no_authentication(self):
        """Test service account token deletion without authentication."""
        # Create request without authenticated user
        request = mock.Mock()
        request.state.auth_user = None

        # Create delete request
        delete_body = payloads.ServiceAccountTokenDeleteBody(
            token_id='token123')

        # Execute deletion and expect authentication error
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await users_server.delete_service_account_token(
                request, delete_body)

        # Verify authentication error
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == 'Authentication required'

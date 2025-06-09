"""Unit tests for the users server endpoints."""

from unittest import mock

import fastapi
import pytest

from sky import models
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import rbac
from sky.users import server
from sky.utils import common


@pytest.fixture
def mock_users():
    """Create mock users for testing."""
    user1 = models.User(id='user1', name='Alice')
    user2 = models.User(id='user2', name='Bob')
    user3 = models.User(id='user3', name='Charlie')
    return [user1, user2, user3]


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = mock.MagicMock(spec=fastapi.Request)
    request.state = mock.MagicMock()
    return request


class TestUsersEndpoints:
    """Test class for users server endpoints."""

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_success(self, mock_get_user_roles,
                                          mock_get_all_users, mock_users):
        """Test successful GET /users endpoint."""
        # Setup
        mock_get_all_users.return_value = mock_users
        mock_get_user_roles.side_effect = [
            ['admin'],  # Alice has admin role
            ['user'],  # Bob has user role
            []  # Charlie has no roles
        ]

        # Execute
        result = await server.users()

        # Verify
        assert len(result) == 3
        assert result[0] == {'id': 'user1', 'name': 'Alice', 'role': 'admin'}
        assert result[1] == {'id': 'user2', 'name': 'Bob', 'role': 'user'}
        assert result[2] == {'id': 'user3', 'name': 'Charlie', 'role': ''}

        # Verify function calls
        mock_get_all_users.assert_called_once()
        assert mock_get_user_roles.call_count == 3
        mock_get_user_roles.assert_any_call('user1')
        mock_get_user_roles.assert_any_call('user2')
        mock_get_user_roles.assert_any_call('user3')

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_empty(self, mock_get_user_roles,
                                        mock_get_all_users):
        """Test GET /users endpoint with no users."""
        # Setup
        mock_get_all_users.return_value = []

        # Execute
        result = await server.users()

        # Verify
        assert result == []
        mock_get_all_users.assert_called_once()
        mock_get_user_roles.assert_not_called()

    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_get_current_user_role_authenticated_user(
            self, mock_get_user_roles, mock_request):
        """Test GET /users/role endpoint with authenticated user."""
        # Setup
        test_user = models.User(id='test_user', name='Test User')
        mock_request.state.auth_user = test_user
        mock_get_user_roles.return_value = ['admin']

        # Execute
        result = await server.get_current_user_role(mock_request)

        # Verify
        assert result == {'name': 'Test User', 'role': 'admin'}
        mock_get_user_roles.assert_called_once_with('test_user')

    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_get_current_user_role_authenticated_user_no_roles(
            self, mock_get_user_roles, mock_request):
        """Test GET /users/role endpoint with authenticated user who has no roles."""
        # Setup
        test_user = models.User(id='test_user', name='Test User')
        mock_request.state.auth_user = test_user
        mock_get_user_roles.return_value = []

        # Execute
        result = await server.get_current_user_role(mock_request)

        # Verify
        assert result == {'name': 'Test User', 'role': ''}
        mock_get_user_roles.assert_called_once_with('test_user')

    @pytest.mark.asyncio
    async def test_get_current_user_role_no_auth_user(self, mock_request):
        """Test GET /users/role endpoint with no authenticated user."""
        # Setup
        mock_request.state.auth_user = None

        # Execute
        result = await server.get_current_user_role(mock_request)

        # Verify - should return admin role when no auth user
        assert result == {'name': '', 'role': rbac.RoleName.ADMIN.value}

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @pytest.mark.asyncio
    async def test_user_update_success(self, mock_update_role, mock_get_user,
                                       mock_get_supported_roles):
        """Test successful POST /users/update endpoint."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user

        update_body = payloads.UserUpdateBody(user_id='test_user', role='admin')

        # Execute
        result = await server.user_update(update_body)

        # Verify
        assert result is None  # Function returns None on success
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @pytest.mark.asyncio
    async def test_user_update_invalid_role(self, mock_get_supported_roles):
        """Test POST /users/update endpoint with invalid role."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              role='invalid_role')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(update_body)

        assert exc_info.value.status_code == 400
        assert 'Invalid role: invalid_role' in str(exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @pytest.mark.asyncio
    async def test_user_update_user_not_found(self, mock_get_user,
                                              mock_get_supported_roles):
        """Test POST /users/update endpoint with non-existent user."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_user.return_value = None

        update_body = payloads.UserUpdateBody(user_id='nonexistent_user',
                                              role='admin')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(update_body)

        assert exc_info.value.status_code == 400
        assert 'User nonexistent_user does not exist' in str(
            exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('nonexistent_user')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @pytest.mark.asyncio
    async def test_user_update_server_user_forbidden(self, mock_get_user,
                                                     mock_get_supported_roles):
        """Test POST /users/update endpoint with server internal user."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        server_user = models.User(id=common.SERVER_ID, name='Server User')
        mock_get_user.return_value = server_user

        update_body = payloads.UserUpdateBody(user_id=common.SERVER_ID,
                                              role='user')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(update_body)

        assert exc_info.value.status_code == 400
        assert 'Cannot update role for internal API server user' in str(
            exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with(common.SERVER_ID)

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @pytest.mark.asyncio
    async def test_user_update_system_user_forbidden(self, mock_get_user,
                                                     mock_get_supported_roles):
        """Test POST /users/update endpoint with system user."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        system_user = models.User(id=constants.SKYPILOT_SYSTEM_USER_ID,
                                  name='System User')
        mock_get_user.return_value = system_user

        update_body = payloads.UserUpdateBody(
            user_id=constants.SKYPILOT_SYSTEM_USER_ID, role='user')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(update_body)

        assert exc_info.value.status_code == 400
        assert 'Cannot update role for internal API server user' in str(
            exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with(constants.SKYPILOT_SYSTEM_USER_ID)

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @pytest.mark.asyncio
    async def test_user_update_update_role_exception(self, mock_update_role,
                                                     mock_get_user,
                                                     mock_get_supported_roles):
        """Test POST /users/update endpoint when update_role raises exception."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        mock_update_role.side_effect = Exception('Database error')

        update_body = payloads.UserUpdateBody(user_id='test_user', role='admin')

        # Execute & Verify
        with pytest.raises(Exception) as exc_info:
            await server.user_update(update_body)

        assert 'Database error' in str(exc_info.value)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_with_multiple_roles(self, mock_get_user_roles,
                                                      mock_get_all_users,
                                                      mock_users):
        """Test GET /users endpoint when user has multiple roles."""
        # Setup
        mock_get_all_users.return_value = mock_users
        mock_get_user_roles.side_effect = [
            ['admin', 'user'],  # Alice has multiple roles - should return first
            ['user'],  # Bob has single role
            ['viewer',
             'guest']  # Charlie has multiple roles - should return first
        ]

        # Execute
        result = await server.users()

        # Verify - should return the first role in the list
        assert len(result) == 3
        assert result[0] == {'id': 'user1', 'name': 'Alice', 'role': 'admin'}
        assert result[1] == {'id': 'user2', 'name': 'Bob', 'role': 'user'}
        assert result[2] == {'id': 'user3', 'name': 'Charlie', 'role': 'viewer'}

    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_get_current_user_role_multiple_roles(self,
                                                        mock_get_user_roles,
                                                        mock_request):
        """Test GET /users/role endpoint when user has multiple roles."""
        # Setup
        test_user = models.User(id='test_user', name='Test User')
        mock_request.state.auth_user = test_user
        mock_get_user_roles.return_value = ['admin', 'user', 'viewer']

        # Execute
        result = await server.get_current_user_role(mock_request)

        # Verify - should return the first role in the list
        assert result == {'name': 'Test User', 'role': 'admin'}
        mock_get_user_roles.assert_called_once_with('test_user')

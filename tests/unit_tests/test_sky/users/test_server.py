"""Unit tests for the users server endpoints."""

import hashlib
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

    @mock.patch('sky.global_user_state.get_tokens_by_user_id')
    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_success(self, mock_get_user_roles,
                                          mock_get_all_users, mock_get_tokens,
                                          mock_users):
        """Test successful GET /users endpoint."""
        # Setup
        mock_get_all_users.return_value = mock_users
        mock_get_user_roles.side_effect = [
            ['admin'],  # Alice has admin role
            ['user'],  # Bob has user role
            []  # Charlie has no roles
        ]
        mock_get_tokens.side_effect = [[], [], []]

        # Execute
        result = await server.users()

        # Verify
        assert len(result) == 3
        assert result[0] == server.UserInfo(
            id='user1',
            name='Alice',
            role='admin',
        )
        assert result[1] == server.UserInfo(
            id='user2',
            name='Bob',
            role='user',
        )
        assert result[2] == server.UserInfo(
            id='user3',
            name='Charlie',
            role='',
        )

        # Verify function calls
        mock_get_all_users.assert_called_once()
        assert mock_get_user_roles.call_count == 3
        mock_get_user_roles.assert_any_call('user1')
        mock_get_user_roles.assert_any_call('user2')
        mock_get_user_roles.assert_any_call('user3')
        assert mock_get_tokens.call_count == 3
        mock_get_tokens.assert_any_call('user1')
        mock_get_tokens.assert_any_call('user2')
        mock_get_tokens.assert_any_call('user3')

    @mock.patch('sky.global_user_state.get_tokens_by_user_id')
    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_empty(self, mock_get_user_roles,
                                        mock_get_all_users, mock_get_tokens):
        """Test GET /users endpoint with no users."""
        # Setup
        mock_get_all_users.return_value = []

        # Execute
        result = await server.users()

        # Verify
        assert result == []
        mock_get_all_users.assert_called_once()
        mock_get_user_roles.assert_not_called()
        mock_get_tokens.assert_not_called()

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
        assert result == {
            'id': 'test_user',
            'name': 'Test User',
            'role': 'admin',
        }
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
        assert result == {
            'id': 'test_user',
            'name': 'Test User',
            'role': '',
        }
        mock_get_user_roles.assert_called_once_with('test_user')

    @pytest.mark.asyncio
    async def test_get_current_user_role_no_auth_user(self, mock_request):
        """Test GET /users/role endpoint with no authenticated user."""
        # Setup
        mock_request.state.auth_user = None

        # Execute
        result = await server.get_current_user_role(mock_request)

        # Verify - should return admin role when no auth user
        assert result == {
            'id': '',
            'name': '',
            'role': rbac.RoleName.ADMIN.value,
        }

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @pytest.mark.asyncio
    async def test_user_update_success_update_role(self, mock_update_role,
                                                   mock_get_user,
                                                   mock_get_supported_roles,
                                                   mock_request):
        """Test successful POST /users/update endpoint."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        mock_request.state.auth_user = None

        update_body = payloads.UserUpdateBody(user_id='test_user', role='admin')

        # Execute
        result = await server.user_update(mock_request, update_body)

        # Verify
        assert result is None  # Function returns None on success
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @pytest.mark.asyncio
    async def test_user_update_success(self, mock_update_role,
                                       mock_add_or_update_user, mock_get_user,
                                       mock_get_supported_roles, mock_request):
        """Test successful POST /users/update endpoint."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user',
                                name='Test User',
                                password='old_password')
        mock_get_user.return_value = test_user
        mock_request.state.auth_user = None

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              role='admin',
                                              password='new_password')

        # Execute
        result = await server.user_update(mock_request, update_body)

        # Verify
        assert result is None  # Function returns None on success
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert user_obj.password.startswith('$apr1$')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @pytest.mark.asyncio
    async def test_user_update_invalid_role(self, mock_get_supported_roles,
                                            mock_request):
        """Test POST /users/update endpoint with invalid role."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_request.state.auth_user = None

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              role='invalid_role')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)

        assert exc_info.value.status_code == 400
        assert 'Invalid role: invalid_role' in str(exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @pytest.mark.asyncio
    async def test_user_update_user_not_found(self, mock_get_user,
                                              mock_get_supported_roles,
                                              mock_request):
        """Test POST /users/update endpoint with non-existent user."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_user.return_value = None
        mock_request.state.auth_user = None

        update_body = payloads.UserUpdateBody(user_id='nonexistent_user',
                                              role='admin')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)

        assert exc_info.value.status_code == 400
        assert 'User nonexistent_user does not exist' in str(
            exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('nonexistent_user')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @pytest.mark.asyncio
    async def test_user_update_update_role_exception(self, mock_update_role,
                                                     mock_get_user,
                                                     mock_get_supported_roles,
                                                     mock_request):
        """Test POST /users/update endpoint when update_role raises exception."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        mock_update_role.side_effect = Exception('Database error')
        mock_request.state.auth_user = None

        update_body = payloads.UserUpdateBody(user_id='test_user', role='admin')

        # Execute & Verify
        with pytest.raises(Exception) as exc_info:
            await server.user_update(mock_request, update_body)

        assert 'Database error' in str(exc_info.value)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')

    @mock.patch('sky.global_user_state.get_tokens_by_user_id')
    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_with_multiple_roles(self, mock_get_user_roles,
                                                      mock_get_all_users,
                                                      mock_get_tokens,
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
        mock_get_tokens.side_effect = [[], [], []]

        # Execute
        result = await server.users()

        # Verify - should return the first role in the list
        assert len(result) == 3
        assert result[0] == server.UserInfo(
            id='user1',
            name='Alice',
            role='admin',
        )
        assert result[1] == server.UserInfo(
            id='user2',
            name='Bob',
            role='user',
        )
        assert result[2] == server.UserInfo(
            id='user3',
            name='Charlie',
            role='viewer',
        )
        assert mock_get_tokens.call_count == 3

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
        assert result == {
            'id': 'test_user',
            'name': 'Test User',
            'role': 'admin',
        }
        mock_get_user_roles.assert_called_once_with('test_user')

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_success(self, mock_is_sa_enabled,
                                       mock_get_default_role,
                                       mock_add_or_update_user,
                                       mock_update_role,
                                       mock_get_supported_roles,
                                       mock_get_user_by_name, mock_request):
        """Test successful POST /users/create endpoint."""
        # Setup
        mock_is_sa_enabled.return_value = False
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        mock_add_or_update_user.return_value = True
        mock_request.state.auth_user = models.User(id='admin_user',
                                                   name='Admin')
        create_body = payloads.UserCreateBody(username='alice',
                                              password='pw123',
                                              role=None)

        # Execute
        result = await server.user_create(mock_request, create_body)

        # Verify
        assert result.creator_user_id == 'admin_user'
        assert result.user_id is not None
        assert result.token_id is None
        assert result.token is None
        assert result.expires_at is None
        mock_get_default_role.assert_called_once()
        mock_add_or_update_user.assert_called_once()
        mock_update_role.assert_called_once()
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.name == 'alice'
        assert user_obj.password.startswith('$apr1$')

    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @pytest.mark.asyncio
    async def test_user_create_user_already_exists(self,
                                                   mock_add_or_update_user,
                                                   mock_is_sa_enabled,
                                                   mock_request):
        """Test POST /users/create endpoint when user already exists."""
        # Setup
        mock_is_sa_enabled.return_value = False
        mock_add_or_update_user.return_value = False
        mock_request.state.auth_user = models.User(id='admin_user',
                                                   name='Admin')
        create_body = payloads.UserCreateBody(username='alice',
                                              password='pw123',
                                              role=None)

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, create_body)
        assert exc_info.value.status_code == 400
        assert 'already exists' in str(exc_info.value.detail)
        mock_add_or_update_user.assert_called_once()

    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @pytest.mark.asyncio
    async def test_user_create_invalid_role(self, mock_get_supported_roles,
                                            mock_get_user_by_name,
                                            mock_is_sa_enabled, mock_request):
        """Test POST /users/create endpoint with invalid role."""
        # Setup
        mock_is_sa_enabled.return_value = False
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_request.state.auth_user = models.User(id='admin_user',
                                                   name='Admin')
        create_body = payloads.UserCreateBody(username='alice',
                                              password='pw123',
                                              role='invalid')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, create_body)
        assert exc_info.value.status_code == 400
        assert 'Invalid role' in str(exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()

    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_missing_username_or_password(
            self, mock_is_sa_enabled, mock_request):
        """Test POST /users/create endpoint with missing username or password."""
        mock_is_sa_enabled.return_value = False
        mock_request.state.auth_user = models.User(id='admin_user',
                                                   name='Admin')

        # Missing username
        create_body = payloads.UserCreateBody(username='',
                                              password='pw123',
                                              role=None)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, create_body)
        assert exc_info.value.status_code == 400
        assert 'Username is required' in str(exc_info.value.detail)

        # Missing password
        create_body = payloads.UserCreateBody(username='alice',
                                              password='',
                                              role=None)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, create_body)
        assert exc_info.value.status_code == 400
        assert 'Password is required' in str(exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.delete_tokens_by_user_id')
    @mock.patch('sky.global_user_state.delete_user')
    @mock.patch('sky.users.permission.permission_service.delete_user')
    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    @pytest.mark.asyncio
    async def test_user_delete_success(self, mock_check_resources,
                                       mock_delete_user_role, mock_delete_user,
                                       mock_delete_tokens, mock_get_user):
        """Test successful POST /users/delete endpoint."""
        # Setup
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        delete_body = payloads.UserDeleteBody(user_id='test_user')

        # Execute
        result = await server.user_delete(delete_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_check_resources.assert_called_once()
        mock_delete_user.assert_called_once_with('test_user')
        mock_delete_user_role.assert_called_once_with('test_user')
        mock_delete_tokens.assert_called_once_with('test_user')

    @mock.patch('sky.global_user_state.get_user')
    @pytest.mark.asyncio
    async def test_user_delete_user_not_found(self, mock_get_user):
        """Test POST /users/delete endpoint with non-existent user."""
        # Setup
        mock_get_user.return_value = None
        delete_body = payloads.UserDeleteBody(user_id='nonexistent_user')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_delete(delete_body)
        assert exc_info.value.status_code == 400
        assert 'does not exist' in str(exc_info.value.detail)
        mock_get_user.assert_called_once_with('nonexistent_user')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_user_update_password_admin_success(self, mock_get_user_roles,
                                                      mock_add_or_update_user,
                                                      mock_get_user,
                                                      mock_get_supported_roles,
                                                      mock_request):
        """Test successful password update by admin for any user."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        # Mock current user as admin
        mock_request.state.auth_user = models.User(id='admin_user',
                                                   name='Admin')
        mock_get_user_roles.return_value = ['admin']

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              password='new_password')

        # Execute
        result = await server.user_update(mock_request, update_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_add_or_update_user.assert_called_once()
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert user_obj.password.startswith('$apr1$')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_user_update_password_self_success(self, mock_get_user_roles,
                                                     mock_add_or_update_user,
                                                     mock_get_user,
                                                     mock_get_supported_roles,
                                                     mock_request):
        """Test successful password update by user for themselves."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        # Mock current user as the same user
        mock_request.state.auth_user = test_user
        mock_get_user_roles.return_value = ['user']

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              password='new_password')

        # Execute
        result = await server.user_update(mock_request, update_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_add_or_update_user.assert_called_once()
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert user_obj.password.startswith('$apr1$')

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_user_update_password_other_user_forbidden(
            self, mock_get_user_roles, mock_get_user, mock_get_supported_roles,
            mock_request):
        """Test password update by non-admin for other user (should fail)."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        target_user = models.User(id='target_user', name='Target User')
        mock_get_user.return_value = target_user
        # Mock current user as different non-admin user
        mock_request.state.auth_user = models.User(id='other_user',
                                                   name='Other User')
        mock_get_user_roles.return_value = ['user']

        update_body = payloads.UserUpdateBody(user_id='target_user',
                                              password='new_password')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)

        assert exc_info.value.status_code == 403
        assert 'Only admin can update password for other users' in str(
            exc_info.value.detail)

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_user_update_no_role_forbidden(self, mock_get_user_roles,
                                                 mock_get_user,
                                                 mock_get_supported_roles,
                                                 mock_request):
        """Test role update by non-admin user (should fail)."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        target_user = models.User(id='target_user', name='Target User')
        mock_get_user.return_value = target_user
        # Mock current user as different non-admin user
        mock_request.state.auth_user = models.User(id='target_user',
                                                   name='Target User')
        mock_get_user_roles.return_value = []

        update_body = payloads.UserUpdateBody(user_id='target_user',
                                              role='admin')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)

        assert exc_info.value.status_code == 403
        assert 'Invalid user' in str(exc_info.value.detail)

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_user_update_role_forbidden(self, mock_get_user_roles,
                                              mock_get_user,
                                              mock_get_supported_roles,
                                              mock_request):
        """Test role update by non-admin user (should fail)."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        target_user = models.User(id='target_user', name='Target User')
        mock_get_user.return_value = target_user
        # Mock current user as different non-admin user
        mock_request.state.auth_user = models.User(id='target_user',
                                                   name='Target User')
        mock_get_user_roles.return_value = ['user']

        update_body = payloads.UserUpdateBody(user_id='target_user',
                                              role='admin')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)

        assert exc_info.value.status_code == 403
        assert 'Only admin can update user role' in str(exc_info.value.detail)

    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @pytest.mark.asyncio
    async def test_user_update_role_and_password_admin_success(
            self, mock_update_role, mock_get_user_roles,
            mock_add_or_update_user, mock_get_user, mock_get_supported_roles,
            mock_request):
        """Test successful combined role and password update by admin."""
        # Setup
        mock_get_supported_roles.return_value = ['admin', 'user']
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        # Mock current user as admin
        mock_request.state.auth_user = models.User(id='admin_user',
                                                   name='Admin')
        mock_get_user_roles.return_value = ['admin']

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              role='user',
                                              password='new_password')

        # Execute
        result = await server.user_update(mock_request, update_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_add_or_update_user.assert_called_once()
        mock_update_role.assert_called_once_with('test_user', 'user')
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert user_obj.password.startswith('$apr1$')

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_user_update_forbidden_internal_users(self,
                                                        mock_get_user_roles,
                                                        mock_get_user,
                                                        mock_request):
        """Test POST /users/update endpoint forbidden for internal users."""
        # Setup
        mock_get_user_roles.return_value = ['admin']
        # Internal server user
        server_user = models.User(id=common.SERVER_ID, name='Server User')
        mock_get_user.return_value = server_user
        mock_request.state.auth_user = models.User(id='admin', name='Admin')
        update_body = payloads.UserUpdateBody(user_id=common.SERVER_ID,
                                              role='user')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)
        assert exc_info.value.status_code == 400
        assert 'Cannot update role for internal API server user' in str(
            exc_info.value.detail)
        # Internal system user
        system_user = models.User(id=constants.SKYPILOT_SYSTEM_USER_ID,
                                  name='System User')
        mock_get_user.return_value = system_user
        update_body = payloads.UserUpdateBody(
            user_id=constants.SKYPILOT_SYSTEM_USER_ID, role='user')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)
        assert exc_info.value.status_code == 400
        assert 'Cannot update role for internal API server user' in str(
            exc_info.value.detail)
        # Password update for system user
        update_body = payloads.UserUpdateBody(
            user_id=constants.SKYPILOT_SYSTEM_USER_ID, password='pw')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_update(mock_request, update_body)
        assert exc_info.value.status_code == 400
        assert 'Cannot update password for internal API server user' in str(
            exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.delete_user')
    @mock.patch('sky.users.permission.permission_service.delete_user')
    @pytest.mark.asyncio
    async def test_user_delete_forbidden_internal_users(self,
                                                        mock_delete_user_role,
                                                        mock_delete_user,
                                                        mock_get_user):
        """Test POST /users/delete endpoint forbidden for internal users."""
        # Internal server user
        server_user = models.User(id=common.SERVER_ID, name='Server User')
        mock_get_user.return_value = server_user
        delete_body = payloads.UserDeleteBody(user_id=common.SERVER_ID)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_delete(delete_body)
        assert exc_info.value.status_code == 400
        assert 'Cannot delete internal API server user' in str(
            exc_info.value.detail)
        # Internal system user
        system_user = models.User(id=constants.SKYPILOT_SYSTEM_USER_ID,
                                  name='System User')
        mock_get_user.return_value = system_user
        delete_body = payloads.UserDeleteBody(
            user_id=constants.SKYPILOT_SYSTEM_USER_ID)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_delete(delete_body)
        assert exc_info.value.status_code == 400
        assert 'Cannot delete internal API server user' in str(
            exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_import_success(self, mock_get_default_role,
                                       mock_add_or_update_user,
                                       mock_update_role,
                                       mock_get_supported_roles,
                                       mock_get_user_by_name):
        """Test successful POST /users/import endpoint."""
        # Setup
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'

        csv_content = """username,password,role
alice,pw123,admin
bob,pw456,user
charlie,pw789,"""

        import_body = payloads.UserImportBody(csv_content=csv_content)

        # Execute
        result = await server.user_import(import_body)

        # Verify
        assert result['success_count'] == 3
        assert result['error_count'] == 0
        assert result['total_processed'] == 3
        assert not result['parse_errors']
        assert not result['creation_errors']

        # Verify function calls
        assert mock_get_user_by_name.call_count == 3
        assert mock_add_or_update_user.call_count == 3
        assert mock_update_role.call_count == 3

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_import_with_errors(self, mock_get_default_role,
                                           mock_add_or_update_user,
                                           mock_update_role,
                                           mock_get_supported_roles,
                                           mock_get_user_by_name):
        """Test POST /users/import endpoint with some errors."""
        # Setup
        mock_get_user_by_name.side_effect = [None, object(), None]  # bob exists
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        mock_add_or_update_user.side_effect = [
            None, Exception('DB error'), None
        ]

        csv_content = """username,password,role
alice,pw123,admin
bob,pw456,user
charlie,pw789,invalid_role"""

        import_body = payloads.UserImportBody(csv_content=csv_content)

        # Execute
        result = await server.user_import(import_body)

        # Verify
        assert result['success_count'] == 1
        assert result['error_count'] == 2
        assert result['total_processed'] == 3
        assert not result['parse_errors']
        assert len(result['creation_errors']) == 2
        assert 'bob: User already exists' in result['creation_errors']
        assert 'charlie: DB error' in result['creation_errors']

    @pytest.mark.asyncio
    async def test_user_import_invalid_csv(self):
        """Test POST /users/import endpoint with invalid CSV."""
        # Missing required headers
        csv_content = """username,password
alice,pw123"""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_import(import_body)
        assert exc_info.value.status_code == 400
        assert 'Missing required columns' in str(exc_info.value.detail)

        # Empty CSV
        csv_content = ""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_import(import_body)
        assert exc_info.value.status_code == 400
        assert 'CSV content is required' in str(exc_info.value.detail)

        # Only header row
        csv_content = "username,password,role"
        import_body = payloads.UserImportBody(csv_content=csv_content)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_import(import_body)
        assert exc_info.value.status_code == 400
        assert 'CSV must have at least a header row and one data row' in str(
            exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_export_success(self, mock_get_default_role,
                                       mock_get_user_roles, mock_get_all_users):
        """Test successful GET /users/export endpoint."""
        # Setup
        users = [
            models.User(id='user1', name='alice', password='hash1'),
            models.User(id='user2', name='bob', password='hash2'),
            models.User(id='user3', name='charlie', password='hash3')
        ]
        mock_get_all_users.return_value = users
        mock_get_default_role.return_value = 'user'
        mock_get_user_roles.side_effect = [
            ['admin'],  # alice
            ['user'],  # bob
            []  # charlie (no role)
        ]

        # Execute
        result = await server.user_export()

        # Verify
        assert result['user_count'] == 3
        csv_lines = result['csv_content'].split('\n')
        assert len(csv_lines) == 4  # header + 3 users
        assert csv_lines[0] == 'username,password,role'
        assert csv_lines[1] == 'alice,hash1,admin'
        assert csv_lines[2] == 'bob,hash2,user'
        assert csv_lines[3] == 'charlie,hash3,user'

        # Verify function calls
        mock_get_all_users.assert_called_once()
        assert mock_get_user_roles.call_count == 3

    @mock.patch('sky.global_user_state.get_all_users')
    @pytest.mark.asyncio
    async def test_user_export_empty(self, mock_get_all_users):
        """Test GET /users/export endpoint with no users."""
        # Setup
        mock_get_all_users.return_value = []

        # Execute
        result = await server.user_export()

        # Verify
        assert result['user_count'] == 0
        csv_lines = result['csv_content'].split('\n')
        assert len(csv_lines) == 1  # only header
        assert csv_lines[0] == 'username,password,role'

        # Verify function calls
        mock_get_all_users.assert_called_once()

    @mock.patch('sky.global_user_state.get_all_users')
    @pytest.mark.asyncio
    async def test_user_export_error(self, mock_get_all_users):
        """Test GET /users/export endpoint with error."""
        # Setup
        mock_get_all_users.side_effect = Exception('Database error')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_export()
        assert exc_info.value.status_code == 500
        assert 'Failed to export users' in str(exc_info.value.detail)
        mock_get_all_users.assert_called_once()

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_import_missing_username_or_password(
            self, mock_get_default_role, mock_add_or_update_user,
            mock_update_role, mock_get_supported_roles, mock_get_user_by_name):
        """Test import with some rows missing username or password."""
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        # Second row missing password, third row valid
        csv_content = """username,password,role
alice,,admin
bob,pw456,user
"""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        result = await server.user_import(import_body)
        # Only bob is imported
        assert result['success_count'] == 1
        assert result['error_count'] == 0
        assert result['total_processed'] == 1
        assert len(result['parse_errors']) == 1
        assert 'Line 2: Username and password are required' in result[
            'parse_errors'][0]

    @pytest.mark.asyncio
    async def test_user_import_all_invalid_rows(self):
        """Test import where all rows are invalid (should raise 400)."""
        csv_content = """username,password,role
,,
,,
"""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_import(import_body)
        assert exc_info.value.status_code == 400
        assert 'No valid users found. Errors:' in str(exc_info.value.detail)
        assert 'Username and password are required' in str(
            exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_import_with_empty_line(self, mock_get_default_role,
                                               mock_add_or_update_user,
                                               mock_update_role,
                                               mock_get_supported_roles,
                                               mock_get_user_by_name):
        """Test import with empty lines in CSV (should be skipped)."""
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        csv_content = """username,password,role
alice,pw123,admin

bob,pw456,user
"""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        result = await server.user_import(import_body)
        assert result['success_count'] == 2
        assert result['error_count'] == 0
        assert result['total_processed'] == 2
        assert not result['parse_errors']
        assert not result['creation_errors']

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_import_with_invalid_column_count(
            self, mock_get_default_role, mock_add_or_update_user,
            mock_update_role, mock_get_supported_roles, mock_get_user_by_name):
        """Test import with a row that has invalid column count (should be parse error)."""
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        csv_content = """username,password,role
alice,$apr1$dUKyQFs/$8ZVZOFWaDzpL0JTKVpI8W0,admin
bob,pw456,user,extra_column
charlie,pw789,user
"""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        result = await server.user_import(import_body)
        # Only alice and charlie are imported, bob's row is invalid
        assert result['success_count'] == 2
        assert result['error_count'] == 0
        assert result['total_processed'] == 2
        assert len(result['parse_errors']) == 1
        assert 'Line 3: Invalid number of columns' in result['parse_errors'][0]

    # New tests for tokens in users()
    @mock.patch('sky.global_user_state.get_tokens_by_user_id')
    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_user_roles')
    @pytest.mark.asyncio
    async def test_users_endpoint_with_tokens(self, mock_get_user_roles,
                                              mock_get_all_users,
                                              mock_get_tokens):
        users = [models.User(id='u1', name='A')]
        mock_get_all_users.return_value = users
        mock_get_user_roles.return_value = ['user']
        mock_get_tokens.return_value = [{
            'token_id': 't1',
            'last_used_at': 111,
            'expires_at': 222,
        }]
        result = await server.users()
        assert len(result) == 1
        assert result[0].token_id == 't1'
        assert result[0].last_used_at == 111
        assert result[0].expires_at == 222

    # New tests for service-account user creation
    @mock.patch('sky.global_user_state.add_service_account_token')
    @mock.patch('sky.users.token_service.token_service.create_token')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_service_account_success(
            self, mock_is_sa_enabled, mock_get_supported_roles,
            mock_get_default_role, mock_add_or_update_user, mock_update_role,
            mock_create_token, mock_add_sa_token, mock_request):
        mock_is_sa_enabled.return_value = True
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        mock_add_or_update_user.return_value = True
        mock_request.state.auth_user = models.User(id='creator', name='Admin')
        mock_create_token.return_value = {
            'token_id': 'tid',
            'token': 'sky_abc',
            'token_hash': 'hash',
            'expires_at': 1234,
        }
        body = payloads.UserCreateBody(username='svc_user',
                                       password='',
                                       role=None,
                                       expires_in_days=30)
        result = await server.user_create(mock_request, body)
        assert result.token_id == 'tid'
        assert result.token == 'sky_abc'
        assert result.expires_at == 1234
        mock_add_sa_token.assert_called_once()

    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_service_account_invalid_expiration(
            self, mock_is_sa_enabled, mock_request):
        mock_is_sa_enabled.return_value = True
        mock_request.state.auth_user = models.User(id='creator', name='Admin')
        body = payloads.UserCreateBody(username='svc_user',
                                       password='',
                                       role=None,
                                       expires_in_days=-1)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, body)
        assert exc_info.value.status_code == 400
        assert 'Expiration days must be positive or 0' in str(
            exc_info.value.detail)

    # Tests for _create_user_and_token helper
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    def test__create_user_and_token_no_sa(self, mock_add_or_update_user,
                                          mock_update_role):
        mock_add_or_update_user.return_value = True
        result = server._create_user_and_token(
            user_id='uid',
            user_name='uname',
            role='user',
            creator_user_id='creator',
            sa_enabled=False,
            password_hash='hash',
        )
        assert result.user_id == 'uid'
        assert result.creator_user_id == 'creator'
        assert result.token is None
        mock_update_role.assert_called_once_with('uid', 'user')

    @mock.patch('sky.global_user_state.add_service_account_token')
    @mock.patch('sky.users.token_service.token_service.create_token')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    def test__create_user_and_token_sa_success(self, mock_add_or_update_user,
                                               mock_update_role,
                                               mock_create_token,
                                               mock_add_sa_token):
        mock_add_or_update_user.return_value = True
        mock_create_token.return_value = {
            'token_id': 'tid',
            'token': 'sky_abc',
            'token_hash': 'hash',
            'expires_at': 1234,
        }
        result = server._create_user_and_token(
            user_id='uid',
            user_name='uname',
            role='user',
            creator_user_id='creator',
            sa_enabled=True,
            password_hash=None,
            expires_in_days=10,
        )
        assert result.user_id == 'uid'
        assert result.token_id == 'tid'
        assert result.token == 'sky_abc'
        mock_add_sa_token.assert_called_once()

    @mock.patch('sky.users.token_service.token_service.create_token')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    def test__create_user_and_token_sa_error(self, mock_add_or_update_user,
                                             mock_update_role,
                                             mock_create_token):
        """_create_user_and_token should surface 500 when token creation fails."""
        mock_add_or_update_user.return_value = True
        mock_create_token.side_effect = Exception('boom')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server._create_user_and_token(user_id='uid',
                                          user_name='uname',
                                          role='user',
                                          creator_user_id='creator',
                                          sa_enabled=True)
        assert exc_info.value.status_code == 500
        assert 'Failed to create user and token' in str(exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.utils.resource_checker.check_no_active_resources_for_users'
               )
    @pytest.mark.asyncio
    async def test_user_delete_active_resources_error(self,
                                                      mock_check_resources,
                                                      mock_get_user):
        """Deletion should fail with 400 when active resources exist."""
        mock_get_user.return_value = models.User(id='u', name='U')
        mock_check_resources.side_effect = ValueError('Active resources found')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_delete(payloads.UserDeleteBody(user_id='u'))
        assert exc_info.value.status_code == 400
        assert 'Active resources found' in str(exc_info.value.detail)

    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_unauthenticated(self, mock_is_sa_enabled,
                                               mock_request):
        """user_create should return 401 when no auth user."""
        mock_is_sa_enabled.return_value = False
        mock_request.state.auth_user = None
        body = payloads.UserCreateBody(username='alice',
                                       password='pw',
                                       role=None)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, body)
        assert exc_info.value.status_code == 401
        assert 'Authentication required' in str(exc_info.value.detail)

    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_invalid_username_regex(self, mock_is_sa_enabled,
                                                      mock_request):
        """user_create should validate username against regex and reject invalid ones."""
        mock_is_sa_enabled.return_value = False
        mock_request.state.auth_user = models.User(id='admin', name='Admin')
        body = payloads.UserCreateBody(username='Invalid Name! ',
                                       password='pw',
                                       role=None)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.user_create(mock_request, body)
        assert exc_info.value.status_code == 400
        # If regex check is bypassed due to whitespace trimming behavior, user creation may proceed
        # to duplicate name check; accept either validation message.
        assert 'Username must match the regex' in str(exc_info.value.detail)

    @mock.patch('sky.global_user_state.add_service_account_token')
    @mock.patch('sky.users.token_service.token_service.create_token')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.utils.common_utils.is_service_account_token_enabled')
    @pytest.mark.asyncio
    async def test_user_create_service_account_never_expire(
            self, mock_is_sa_enabled, mock_get_supported_roles,
            mock_get_default_role, mock_add_or_update_user, mock_update_role,
            mock_create_token, mock_add_sa_token, mock_request):
        """expires_in_days=0 should be treated as never expire (None passed to token creation)."""
        mock_is_sa_enabled.return_value = True
        mock_get_supported_roles.return_value = ['user']
        mock_get_default_role.return_value = 'user'
        mock_add_or_update_user.return_value = True
        mock_request.state.auth_user = models.User(id='creator', name='Admin')
        mock_create_token.return_value = {
            'token_id': 'tid',
            'token': 'sky_abc',
            'token_hash': 'hash',
            'expires_at': None,
        }
        body = payloads.UserCreateBody(username='svcuser',
                                       password='',
                                       role=None,
                                       expires_in_days=0)
        result = await server.user_create(mock_request, body)
        assert result.token_id == 'tid'
        assert result.token == 'sky_abc'
        # Verify create_token called with expires_in_days=None
        args, kwargs = mock_create_token.call_args
        assert kwargs.get('expires_in_days', 'missing') is None
        mock_add_sa_token.assert_called_once()

    @mock.patch('sky.global_user_state.add_or_update_user')
    @pytest.mark.asyncio
    async def test_create_service_account_token_invalid_name(
            self, mock_add_or_update_user, mock_request):
        mock_request.state.auth_user = models.User(id='u', name='U')
        mock_add_or_update_user.return_value = True
        body = payloads.ServiceAccountTokenCreateBody(
            token_name='Invalid Name!')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await server.create_service_account_token(mock_request, body)
        assert exc_info.value.status_code == 400
        assert 'Token name must match the regex' in str(exc_info.value.detail)

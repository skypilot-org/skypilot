"""Unit tests for the users server endpoints."""

import hashlib
from unittest import mock

import fastapi
import pytest

from sky import models
from sky.server import common as server_common
from sky.server.requests import payloads
from sky.skylet import constants
from sky.users import rbac
from sky.users import server
from sky.utils import common


class TestGetUserType:
    """Test class for get_user_type function."""

    def test_service_account_user(self):
        """Test that service account users return 'sa' type."""
        # Service accounts have IDs starting with "sa-"
        user = models.User(id='sa-test-token', name='test-service-account')
        result = server.get_user_type(user)
        assert result == models.UserType.SA.value

    def test_server_id_system_user(self):
        """Test that SERVER_ID user returns 'system' type."""
        user = models.User(id=common.SERVER_ID, name='Server')
        result = server.get_user_type(user)
        assert result == models.UserType.SYSTEM.value

    def test_skypilot_system_user(self):
        """Test that SKYPILOT_SYSTEM_USER_ID user returns 'system' type."""
        user = models.User(id=constants.SKYPILOT_SYSTEM_USER_ID, name='System')
        result = server.get_user_type(user)
        assert result == models.UserType.SYSTEM.value

    def test_basic_auth_user_with_password(self):
        """Test that users with password return 'basic' type."""
        user = models.User(id='user123', name='alice', password='hashed_pw')
        result = server.get_user_type(user)
        assert result == models.UserType.BASIC.value

    def test_sso_user_with_email(self):
        """Test that users with email in name return 'sso' type."""
        user = models.User(id='user456', name='alice@example.com')
        result = server.get_user_type(user)
        assert result == models.UserType.SSO.value

    def test_legacy_user_default(self):
        """Test that users without special attributes return 'legacy' type."""
        user = models.User(id='user789', name='bob')
        result = server.get_user_type(user)
        assert result == models.UserType.LEGACY.value

    def test_legacy_user_with_none_password(self):
        """Test that users with None password return appropriate type."""
        user = models.User(id='user789', name='bob', password=None)
        result = server.get_user_type(user)
        assert result == models.UserType.LEGACY.value

    def test_priority_service_account_over_system(self):
        """Test that service account check takes priority."""
        # Even if ID matches system user pattern, SA prefix should take priority
        user = models.User(id='sa-system', name='service')
        result = server.get_user_type(user)
        assert result == models.UserType.SA.value

    def test_priority_system_over_basic(self):
        """Test that system user check takes priority over basic auth."""
        # System user with password should still be system type
        user = models.User(id=common.SERVER_ID,
                           name='Server',
                           password='some_pw')
        result = server.get_user_type(user)
        assert result == models.UserType.SYSTEM.value

    def test_priority_basic_over_sso(self):
        """Test that basic auth check takes priority over SSO."""
        # User with both password and email should be basic type
        user = models.User(id='user123',
                           name='alice@example.com',
                           password='hashed_pw')
        result = server.get_user_type(user)
        assert result == models.UserType.BASIC.value

    def test_priority_sso_over_legacy(self):
        """Test that SSO check takes priority over legacy."""
        # User with email but no password should be SSO type
        user = models.User(id='user123', name='alice@company.org')
        result = server.get_user_type(user)
        assert result == models.UserType.SSO.value

    def test_user_with_empty_name(self):
        """Test user with empty name returns legacy type."""
        user = models.User(id='user123', name='')
        result = server.get_user_type(user)
        assert result == models.UserType.LEGACY.value

    def test_user_with_none_name(self):
        """Test user with None name returns legacy type."""
        user = models.User(id='user123', name=None)
        result = server.get_user_type(user)
        assert result == models.UserType.LEGACY.value


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
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @pytest.mark.asyncio
    async def test_users_endpoint_success(self, mock_get_users_for_role,
                                          mock_get_all_users, mock_users):
        """Test successful GET /users endpoint."""
        # Setup
        mock_get_all_users.return_value = mock_users

        def mock_users_for_role_side_effect(role):
            role_mappings = {
                'admin': ['user1'],  # Alice has admin role
                'user': ['user2'],  # Bob has user role
                # Charlie (user3) has no roles
            }
            return role_mappings.get(role, [])

        mock_get_users_for_role.side_effect = mock_users_for_role_side_effect

        # Execute
        result = server.users()

        # Verify
        assert len(result) == 3
        assert result[0] == {
            'id': 'user1',
            'name': 'Alice',
            'role': 'admin',
            'created_at': None,
            'user_type': 'legacy',
        }
        assert result[1] == {
            'id': 'user2',
            'name': 'Bob',
            'role': 'user',
            'created_at': None,
            'user_type': 'legacy',
        }
        assert result[2] == {
            'id': 'user3',
            'name': 'Charlie',
            'role': '',
            'created_at': None,
            'user_type': 'legacy',
        }

        # Verify function calls
        mock_get_all_users.assert_called_once()
        # Should call get_users_for_role for each supported role
        assert mock_get_users_for_role.call_count == 2  # admin and user roles
        mock_get_users_for_role.assert_any_call('admin')
        mock_get_users_for_role.assert_any_call('user')

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @pytest.mark.asyncio
    async def test_users_endpoint_empty(self, mock_get_users_for_role,
                                        mock_get_all_users):
        """Test GET /users endpoint with no users."""
        # Setup
        mock_get_all_users.return_value = []
        mock_get_users_for_role.return_value = []

        # Execute
        result = server.users()

        # Verify
        assert result == []
        mock_get_all_users.assert_called_once()
        # Should still call get_users_for_role for each supported role even with no users
        assert mock_get_users_for_role.call_count == 2  # admin and user roles

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
        result = server.get_current_user_role(mock_request)

        # Verify
        assert result == {
            'id': 'test_user',
            'name': 'Test User',
            'role': 'admin'
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
        result = server.get_current_user_role(mock_request)

        # Verify
        assert result == {'id': 'test_user', 'name': 'Test User', 'role': ''}
        mock_get_user_roles.assert_called_once_with('test_user')

    @pytest.mark.asyncio
    async def test_get_current_user_role_no_auth_user(self, mock_request):
        """Test GET /users/role endpoint with no authenticated user."""
        # Setup
        mock_request.state.auth_user = None

        # Execute
        result = server.get_current_user_role(mock_request)

        # Verify - should return admin role when no auth user
        assert result == {
            'id': '',
            'name': '',
            'role': rbac.RoleName.ADMIN.value
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
        result = server.user_update(mock_request, update_body)

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
        new_password = 'new_password'

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              role='admin',
                                              password=new_password)

        # Execute
        result = server.user_update(mock_request, update_body)

        # Verify
        assert result is None  # Function returns None on success
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert server_common.crypt_ctx.identify(user_obj.password) is not None
        assert user_obj.password != new_password

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
            server.user_update(mock_request, update_body)

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
            server.user_update(mock_request, update_body)

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
            server.user_update(mock_request, update_body)

        assert 'Database error' in str(exc_info.value)
        mock_get_supported_roles.assert_called_once()
        mock_get_user.assert_called_once_with('test_user')
        mock_update_role.assert_called_once_with('test_user', 'admin')

    @mock.patch('sky.global_user_state.get_all_users')
    @mock.patch('sky.users.permission.permission_service.get_users_for_role')
    @pytest.mark.asyncio
    async def test_users_endpoint_with_multiple_roles(self,
                                                      mock_get_users_for_role,
                                                      mock_get_all_users,
                                                      mock_users):
        """Test GET /users endpoint when user has multiple roles."""
        # Setup
        mock_get_all_users.return_value = mock_users

        def mock_users_for_role_side_effect(role):
            role_mappings = {
                'admin': ['user1'],  # Alice has admin role
                'user': ['user2', 'user3'],  # Bob and Charlie have user role
            }
            return role_mappings.get(role, [])

        mock_get_users_for_role.side_effect = mock_users_for_role_side_effect

        # Execute
        result = server.users()

        assert len(result) == 3
        assert result[0] == {
            'id': 'user1',
            'name': 'Alice',
            'role': 'admin',
            'created_at': None,
            'user_type': 'legacy',
        }
        assert result[1] == {
            'id': 'user2',
            'name': 'Bob',
            'role': 'user',
            'created_at': None,
            'user_type': 'legacy',
        }
        assert result[2] == {
            'id': 'user3',
            'name': 'Charlie',
            'role': 'user',
            'created_at': None,
            'user_type': 'legacy',
        }

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
        result = server.get_current_user_role(mock_request)

        # Verify - should return the first role in the list
        assert result == {
            'id': 'test_user',
            'name': 'Test User',
            'role': 'admin'
        }
        mock_get_user_roles.assert_called_once_with('test_user')

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @mock.patch('sky.users.permission.permission_service.update_role')
    @mock.patch('sky.global_user_state.add_or_update_user')
    @mock.patch('sky.users.rbac.get_default_role')
    @pytest.mark.asyncio
    async def test_user_create_success(self, mock_get_default_role,
                                       mock_add_or_update_user,
                                       mock_update_role,
                                       mock_get_supported_roles,
                                       mock_get_user_by_name):
        """Test successful POST /users/create endpoint."""
        # Setup
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        mock_get_default_role.return_value = 'user'
        password = 'pw123'
        create_body = payloads.UserCreateBody(username='alice',
                                              password=password,
                                              role=None)

        # Execute
        result = server.user_create(create_body)

        # Verify
        assert result is None
        mock_get_user_by_name.assert_called_once_with('alice')
        mock_get_default_role.assert_called_once()
        mock_add_or_update_user.assert_called_once()
        mock_update_role.assert_called_once()
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.name == 'alice'
        assert server_common.crypt_ctx.identify(user_obj.password) is not None
        assert user_obj.password != password

    @mock.patch('sky.global_user_state.get_user_by_name')
    @pytest.mark.asyncio
    async def test_user_create_user_already_exists(self, mock_get_user_by_name):
        """Test POST /users/create endpoint when user already exists."""
        # Setup
        mock_get_user_by_name.return_value = object()
        create_body = payloads.UserCreateBody(username='alice',
                                              password='pw123',
                                              role=None)

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_create(create_body)
        assert exc_info.value.status_code == 400
        assert 'already exists' in str(exc_info.value.detail)
        mock_get_user_by_name.assert_called_once_with('alice')

    @mock.patch('sky.global_user_state.get_user_by_name')
    @mock.patch('sky.users.rbac.get_supported_roles')
    @pytest.mark.asyncio
    async def test_user_create_invalid_role(self, mock_get_supported_roles,
                                            mock_get_user_by_name):
        """Test POST /users/create endpoint with invalid role."""
        # Setup
        mock_get_user_by_name.return_value = None
        mock_get_supported_roles.return_value = ['admin', 'user']
        create_body = payloads.UserCreateBody(username='alice',
                                              password='pw123',
                                              role='invalid')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_create(create_body)
        assert exc_info.value.status_code == 400
        assert 'Invalid role' in str(exc_info.value.detail)
        mock_get_supported_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_create_missing_username_or_password(self):
        """Test POST /users/create endpoint with missing username or password."""
        # Missing username
        create_body = payloads.UserCreateBody(username='',
                                              password='pw123',
                                              role=None)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_create(create_body)
        assert exc_info.value.status_code == 400
        assert 'Username and password are required' in str(
            exc_info.value.detail)

        # Missing password
        create_body = payloads.UserCreateBody(username='alice',
                                              password='',
                                              role=None)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_create(create_body)
        assert exc_info.value.status_code == 400
        assert 'Username and password are required' in str(
            exc_info.value.detail)

    @mock.patch('sky.global_user_state.get_user')
    @mock.patch('sky.global_user_state.delete_user')
    @mock.patch('sky.users.permission.permission_service.delete_user')
    @pytest.mark.asyncio
    async def test_user_delete_success(self, mock_delete_user_role,
                                       mock_delete_user, mock_get_user):
        """Test successful POST /users/delete endpoint."""
        # Setup
        test_user = models.User(id='test_user', name='Test User')
        mock_get_user.return_value = test_user
        delete_body = payloads.UserDeleteBody(user_id='test_user')

        # Execute
        result = server.user_delete(delete_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_delete_user.assert_called_once_with('test_user')
        mock_delete_user_role.assert_called_once_with('test_user')

    @mock.patch('sky.global_user_state.get_user')
    @pytest.mark.asyncio
    async def test_user_delete_user_not_found(self, mock_get_user):
        """Test POST /users/delete endpoint with non-existent user."""
        # Setup
        mock_get_user.return_value = None
        delete_body = payloads.UserDeleteBody(user_id='nonexistent_user')

        # Execute & Verify
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_delete(delete_body)
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
        new_password = 'new_password'

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              password=new_password)

        # Execute
        result = server.user_update(mock_request, update_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_add_or_update_user.assert_called_once()
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert server_common.crypt_ctx.identify(user_obj.password) is not None
        assert user_obj.password != new_password

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
        new_password = 'new_password'

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              password=new_password)

        # Execute
        result = server.user_update(mock_request, update_body)

        # Verify
        assert result is None
        mock_get_user.assert_called_once_with('test_user')
        mock_add_or_update_user.assert_called_once()
        # Check password hash
        args, kwargs = mock_add_or_update_user.call_args
        user_obj = args[0]
        assert user_obj.id == 'test_user'
        assert user_obj.name == 'Test User'
        assert server_common.crypt_ctx.identify(user_obj.password) is not None
        assert user_obj.password != new_password

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
            server.user_update(mock_request, update_body)

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
            server.user_update(mock_request, update_body)

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
            server.user_update(mock_request, update_body)

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
        new_password = 'new_password'

        update_body = payloads.UserUpdateBody(user_id='test_user',
                                              role='user',
                                              password=new_password)

        # Execute
        result = server.user_update(mock_request, update_body)

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
        assert server_common.crypt_ctx.identify(user_obj.password) is not None
        assert user_obj.password != new_password

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
            server.user_update(mock_request, update_body)
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
            server.user_update(mock_request, update_body)
        assert exc_info.value.status_code == 400
        assert 'Cannot update role for internal API server user' in str(
            exc_info.value.detail)
        # Password update for system user
        update_body = payloads.UserUpdateBody(
            user_id=constants.SKYPILOT_SYSTEM_USER_ID, password='pw')
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_update(mock_request, update_body)
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
            server.user_delete(delete_body)
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
            server.user_delete(delete_body)
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
        result = server.user_import(import_body)

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
        result = server.user_import(import_body)

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
            server.user_import(import_body)
        assert exc_info.value.status_code == 400
        assert 'Missing required columns' in str(exc_info.value.detail)

        # Empty CSV
        csv_content = ""
        import_body = payloads.UserImportBody(csv_content=csv_content)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_import(import_body)
        assert exc_info.value.status_code == 400
        assert 'CSV content is required' in str(exc_info.value.detail)

        # Only header row
        csv_content = "username,password,role"
        import_body = payloads.UserImportBody(csv_content=csv_content)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            server.user_import(import_body)
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
        result = server.user_export()

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
        result = server.user_export()

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
            server.user_export()
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
        result = server.user_import(import_body)
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
            server.user_import(import_body)
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
        result = server.user_import(import_body)
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
        result = server.user_import(import_body)
        # Only alice and charlie are imported, bob's row is invalid
        assert result['success_count'] == 2
        assert result['error_count'] == 0
        assert result['total_processed'] == 2
        assert len(result['parse_errors']) == 1
        assert 'Line 3: Invalid number of columns' in result['parse_errors'][0]

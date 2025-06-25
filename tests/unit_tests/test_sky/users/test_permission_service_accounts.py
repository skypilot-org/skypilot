"""Unit tests for service account permission functionality."""

import unittest
from unittest import mock

from sky.users import permission
from sky.users import rbac


class TestServiceAccountPermissions(unittest.TestCase):
    """Test service account permission functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock permission service
        self.permission_service = permission.PermissionService()
        
        # Mock the enforcer to avoid requiring actual Casbin setup
        self.mock_enforcer = mock.MagicMock()
        self.permission_service.enforcer = self.mock_enforcer

    def test_check_service_account_token_permission_same_user(self):
        """Test permission check allows user to manage their own tokens."""
        user_id = 'user123'
        token_owner_id = 'user123'  # Same user
        action = 'delete'
        
        result = self.permission_service.check_service_account_token_permission(
            user_id, token_owner_id, action
        )
        
        self.assertTrue(result)

    def test_check_service_account_token_permission_different_user_admin(self):
        """Test permission check allows admin to manage any tokens."""
        user_id = 'admin_user'
        token_owner_id = 'other_user'
        action = 'delete'
        
        # Mock get_user_roles to return admin role
        with mock.patch.object(self.permission_service, 'get_user_roles') as mock_get_roles:
            mock_get_roles.return_value = [rbac.RoleName.ADMIN.value]
            
            result = self.permission_service.check_service_account_token_permission(
                user_id, token_owner_id, action
            )
            
            self.assertTrue(result)
            mock_get_roles.assert_called_once_with(user_id)

    def test_check_service_account_token_permission_different_user_not_admin(self):
        """Test permission check denies non-admin users from managing others' tokens."""
        user_id = 'regular_user'
        token_owner_id = 'other_user'
        action = 'delete'
        
        # Mock get_user_roles to return non-admin role
        with mock.patch.object(self.permission_service, 'get_user_roles') as mock_get_roles:
            mock_get_roles.return_value = [rbac.RoleName.USER.value]
            
            result = self.permission_service.check_service_account_token_permission(
                user_id, token_owner_id, action
            )
            
            self.assertFalse(result)
            mock_get_roles.assert_called_once_with(user_id)

    def test_check_service_account_token_permission_different_user_no_role(self):
        """Test permission check denies users with no roles from managing others' tokens."""
        user_id = 'no_role_user'
        token_owner_id = 'other_user'
        action = 'delete'
        
        # Mock get_user_roles to return empty list
        with mock.patch.object(self.permission_service, 'get_user_roles') as mock_get_roles:
            mock_get_roles.return_value = []
            
            result = self.permission_service.check_service_account_token_permission(
                user_id, token_owner_id, action
            )
            
            self.assertFalse(result)
            mock_get_roles.assert_called_once_with(user_id)

    def test_check_service_account_token_permission_action_ignored(self):
        """Test that action parameter is ignored (all actions have same permission)."""
        user_id = 'user123'
        token_owner_id = 'user123'
        
        # Test different actions - should all return True for same user
        for action in ['delete', 'view', 'update', 'rotate', 'some_other_action']:
            result = self.permission_service.check_service_account_token_permission(
                user_id, token_owner_id, action
            )
            self.assertTrue(result, f"Failed for action: {action}")

    def test_check_service_account_token_permission_admin_multiple_roles(self):
        """Test permission check works when user has multiple roles including admin."""
        user_id = 'multi_role_user'
        token_owner_id = 'other_user'
        action = 'delete'
        
        # Mock get_user_roles to return multiple roles including admin
        with mock.patch.object(self.permission_service, 'get_user_roles') as mock_get_roles:
            mock_get_roles.return_value = [rbac.RoleName.USER.value, rbac.RoleName.ADMIN.value]
            
            result = self.permission_service.check_service_account_token_permission(
                user_id, token_owner_id, action
            )
            
            self.assertTrue(result)

    def test_check_service_account_token_permission_case_sensitivity(self):
        """Test that role checking is case sensitive."""
        user_id = 'case_sensitive_user'
        token_owner_id = 'other_user'
        action = 'delete'
        
        # Mock get_user_roles to return incorrectly cased admin role
        with mock.patch.object(self.permission_service, 'get_user_roles') as mock_get_roles:
            mock_get_roles.return_value = ['Admin']  # Wrong case
            
            result = self.permission_service.check_service_account_token_permission(
                user_id, token_owner_id, action
            )
            
            self.assertFalse(result)

    def test_check_service_account_token_permission_empty_user_ids(self):
        """Test permission check with empty user IDs."""
        # Empty user ID trying to access other user's token
        result1 = self.permission_service.check_service_account_token_permission(
            '', 'other_user', 'delete'
        )
        self.assertFalse(result1)
        
        # User trying to access empty token owner
        result2 = self.permission_service.check_service_account_token_permission(
            'user123', '', 'delete'
        )
        self.assertFalse(result2)
        
        # Both empty - should be considered same user
        result3 = self.permission_service.check_service_account_token_permission(
            '', '', 'delete'
        )
        self.assertTrue(result3)

    def test_check_service_account_token_permission_none_user_ids(self):
        """Test permission check with None user IDs."""
        # None user ID trying to access other user's token
        result1 = self.permission_service.check_service_account_token_permission(
            None, 'other_user', 'delete'  # type: ignore
        )
        self.assertFalse(result1)
        
        # User trying to access None token owner
        result2 = self.permission_service.check_service_account_token_permission(
            'user123', None, 'delete'  # type: ignore
        )
        self.assertFalse(result2)
        
        # Both None - should be considered same user
        result3 = self.permission_service.check_service_account_token_permission(
            None, None, 'delete'  # type: ignore
        )
        self.assertTrue(result3)


if __name__ == '__main__':
    unittest.main()
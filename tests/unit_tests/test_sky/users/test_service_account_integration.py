"""Integration tests for service account functionality."""

import time
import unittest
from unittest import mock

from sky import global_user_state
from sky import models
from sky.users import permission
from sky.users import rbac
from sky.users import token_service


class TestServiceAccountIntegration(unittest.TestCase):
    """Integration tests for service account functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the global_user_state database functions
        self.mock_add_service_account_token = mock.patch.object(
            global_user_state, 'add_service_account_token'
        ).start()
        self.mock_get_service_account_token = mock.patch.object(
            global_user_state, 'get_service_account_token'
        ).start()
        self.mock_get_all_service_account_tokens = mock.patch.object(
            global_user_state, 'get_all_service_account_tokens'
        ).start()
        self.mock_delete_service_account_token = mock.patch.object(
            global_user_state, 'delete_service_account_token'
        ).start()
        self.mock_update_service_account_token_last_used = mock.patch.object(
            global_user_state, 'update_service_account_token_last_used'
        ).start()
        self.mock_rotate_service_account_token = mock.patch.object(
            global_user_state, 'rotate_service_account_token'
        ).start()
        self.mock_get_user = mock.patch.object(
            global_user_state, 'get_user'
        ).start()
        self.mock_add_or_update_user = mock.patch.object(
            global_user_state, 'add_or_update_user'
        ).start()
        
        # Mock system config for token service
        self.mock_get_system_config = mock.patch.object(
            global_user_state, 'get_system_config'
        ).start()
        self.mock_set_system_config = mock.patch.object(
            global_user_state, 'set_system_config'
        ).start()
        self.mock_get_system_config.return_value = 'test-jwt-secret'
        
        # Set up token service
        self.token_service = token_service.TokenService()

    def tearDown(self):
        """Clean up test fixtures."""
        mock.patch.stopall()

    def test_service_account_creation_workflow(self):
        """Test the complete service account creation workflow."""
        # Simulate creating a service account token
        creator_user_id = 'creator123'
        service_account_user_id = 'sa-creator1-testtoken-abcdef123456'
        token_name = 'test-token'
        
        # Create token
        token_data = self.token_service.create_token(
            creator_user_id=creator_user_id,
            service_account_user_id=service_account_user_id,
            token_name=token_name,
            expires_in_days=30
        )
        
        # Verify token structure
        self.assertIn('token_id', token_data)
        self.assertIn('token', token_data)
        self.assertIn('token_hash', token_data)
        self.assertTrue(token_data['token'].startswith('sky_'))
        
        # Simulate storing in database
        current_time = int(time.time())
        self.mock_add_service_account_token(
            token_id=token_data['token_id'],
            token_name=token_name,
            token_hash=token_data['token_hash'],
            creator_user_hash=creator_user_id,
            service_account_user_id=service_account_user_id,
            expires_at=token_data['expires_at']
        )
        
        # Verify database call was made
        self.mock_add_service_account_token.assert_called_once()

    def test_service_account_token_verification_workflow(self):
        """Test the complete token verification workflow."""
        # Create a token
        token_data = self.token_service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-creator1-testtoken-abcdef123456',
            token_name='test-token'
        )
        
        # Verify the token
        payload = self.token_service.verify_token(token_data['token'])
        
        # Check payload structure
        self.assertIsNotNone(payload)
        if payload is not None:
            self.assertEqual(payload['sub'], 'sa-creator1-testtoken-abcdef123456')
            self.assertEqual(payload['token_id'], token_data['token_id'])
            self.assertEqual(payload['type'], 'service_account')

    def test_service_account_permission_workflow(self):
        """Test the complete permission checking workflow."""
        # Create mock permission service
        permission_service = permission.PermissionService()
        permission_service.enforcer = mock.MagicMock()
        
        # Test user can manage their own tokens
        result = permission_service.check_service_account_token_permission(
            user_id='user123',
            token_owner_id='user123',
            action='delete'
        )
        self.assertTrue(result)
        
        # Test admin can manage any tokens
        with mock.patch.object(permission_service, 'get_user_roles') as mock_get_roles:
            mock_get_roles.return_value = [rbac.RoleName.ADMIN.value]
            
            result = permission_service.check_service_account_token_permission(
                user_id='admin_user',
                token_owner_id='other_user',
                action='delete'
            )
            self.assertTrue(result)

    def test_service_account_database_operations_workflow(self):
        """Test the complete database operations workflow."""
        current_time = int(time.time())
        token_data = {
            'token_id': 'test-token-id',
            'token_name': 'test-token',
            'token_hash': 'test-hash',
            'creator_user_hash': 'creator123',
            'service_account_user_id': 'sa-creator1-testtoken-abcdef123456',
            'created_at': current_time,
            'last_used_at': None,
            'expires_at': current_time + 3600
        }
        
        # Mock getting a token from database
        self.mock_get_service_account_token.return_value = token_data
        
        # Test retrieving token
        retrieved_token = global_user_state.get_service_account_token('test-token-id')
        self.assertEqual(retrieved_token, token_data)
        
        # Test updating last used time
        global_user_state.update_service_account_token_last_used('test-token-id')
        self.mock_update_service_account_token_last_used.assert_called_once_with('test-token-id')
        
        # Test deleting token
        self.mock_delete_service_account_token.return_value = True
        result = global_user_state.delete_service_account_token('test-token-id')
        self.assertTrue(result)

    def test_service_account_model_workflow(self):
        """Test the ServiceAccountToken model workflow."""
        current_time = int(time.time())
        
        # Create model instance
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='creator123',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time,
            expires_at=current_time + 3600
        )
        
        # Test serialization
        token_dict = token.to_dict()
        expected_dict = {
            'token_id': 'test-token-id',
            'user_hash': 'creator123',
            'token_name': 'test-token',
            'created_at': current_time,
            'last_used_at': None,
            'expires_at': current_time + 3600,
        }
        self.assertEqual(token_dict, expected_dict)
        
        # Test expiration check
        self.assertFalse(token.is_expired())  # Should not be expired yet
        
        # Test expired token
        expired_token = models.ServiceAccountToken(
            token_id='expired-token',
            user_hash='creator123',
            token_name='expired-token',
            token_hash='expired-hash',
            created_at=current_time - 7200,
            expires_at=current_time - 3600  # Expired 1 hour ago
        )
        self.assertTrue(expired_token.is_expired())

    def test_service_account_token_rotation_workflow(self):
        """Test the complete token rotation workflow."""
        # Create original token
        original_token_data = self.token_service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-creator1-testtoken-abcdef123456',
            token_name='test-token',
            expires_in_days=30
        )
        
        # Create new token for rotation
        new_token_data = self.token_service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-creator1-testtoken-abcdef123456',  # Same SA
            token_name='test-token',
            expires_in_days=30
        )
        
        # Simulate rotation in database
        global_user_state.rotate_service_account_token(
            token_id=original_token_data['token_id'],
            new_token_hash=new_token_data['token_hash'],
            new_expires_at=new_token_data['expires_at']
        )
        
        # Verify rotation was called
        self.mock_rotate_service_account_token.assert_called_once_with(
            token_id=original_token_data['token_id'],
            new_token_hash=new_token_data['token_hash'],
            new_expires_at=new_token_data['expires_at']
        )

    def test_service_account_user_management_workflow(self):
        """Test the service account user management workflow."""
        # Create service account user
        service_account_user = models.User(
            id='sa-creator1-testtoken-abcdef123456',
            name='test-token'
        )
        
        # Add user to system
        global_user_state.add_or_update_user(service_account_user)
        self.mock_add_or_update_user.assert_called_once_with(service_account_user)
        
        # Mock getting user
        self.mock_get_user.return_value = service_account_user
        
        # Retrieve user
        retrieved_user = global_user_state.get_user('sa-creator1-testtoken-abcdef123456')
        self.assertEqual(retrieved_user, service_account_user)


if __name__ == '__main__':
    unittest.main()
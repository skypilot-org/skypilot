"""Unit tests for service account models."""

import time
import unittest

from sky import models


class TestServiceAccountToken(unittest.TestCase):
    """Test ServiceAccountToken dataclass."""

    def test_service_account_token_creation(self):
        """Test ServiceAccountToken can be created with all fields."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time,
            last_used_at=current_time,
            expires_at=current_time + 3600
        )
        
        self.assertEqual(token.token_id, 'test-token-id')
        self.assertEqual(token.user_hash, 'test-user-hash')
        self.assertEqual(token.token_name, 'test-token')
        self.assertEqual(token.token_hash, 'test-hash')
        self.assertEqual(token.created_at, current_time)
        self.assertEqual(token.last_used_at, current_time)
        self.assertEqual(token.expires_at, current_time + 3600)

    def test_service_account_token_creation_minimal(self):
        """Test ServiceAccountToken can be created with minimal fields."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time
        )
        
        self.assertEqual(token.token_id, 'test-token-id')
        self.assertEqual(token.user_hash, 'test-user-hash')
        self.assertEqual(token.token_name, 'test-token')
        self.assertEqual(token.token_hash, 'test-hash')
        self.assertEqual(token.created_at, current_time)
        self.assertIsNone(token.last_used_at)
        self.assertIsNone(token.expires_at)

    def test_to_dict(self):
        """Test ServiceAccountToken.to_dict() method."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time,
            last_used_at=current_time,
            expires_at=current_time + 3600
        )
        
        expected_dict = {
            'token_id': 'test-token-id',
            'user_hash': 'test-user-hash',
            'token_name': 'test-token',
            'created_at': current_time,
            'last_used_at': current_time,
            'expires_at': current_time + 3600,
        }
        
        self.assertEqual(token.to_dict(), expected_dict)

    def test_to_dict_with_none_values(self):
        """Test ServiceAccountToken.to_dict() with None values."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time
        )
        
        expected_dict = {
            'token_id': 'test-token-id',
            'user_hash': 'test-user-hash',
            'token_name': 'test-token',
            'created_at': current_time,
            'last_used_at': None,
            'expires_at': None,
        }
        
        self.assertEqual(token.to_dict(), expected_dict)

    def test_is_expired_no_expiration(self):
        """Test is_expired() returns False when expires_at is None."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time
        )
        
        self.assertFalse(token.is_expired())

    def test_is_expired_future_expiration(self):
        """Test is_expired() returns False when token expires in the future."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time,
            expires_at=current_time + 3600  # 1 hour in the future
        )
        
        self.assertFalse(token.is_expired())

    def test_is_expired_past_expiration(self):
        """Test is_expired() returns True when token has expired."""
        current_time = int(time.time())
        token = models.ServiceAccountToken(
            token_id='test-token-id',
            user_hash='test-user-hash',
            token_name='test-token',
            token_hash='test-hash',
            created_at=current_time - 7200,  # 2 hours ago
            expires_at=current_time - 3600   # 1 hour ago
        )
        
        self.assertTrue(token.is_expired())


if __name__ == '__main__':
    unittest.main()
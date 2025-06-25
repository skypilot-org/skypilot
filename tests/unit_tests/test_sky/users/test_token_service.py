"""Unit tests for service account token service."""

import datetime
import hashlib
import os
import tempfile
import time
import unittest
from unittest import mock

try:
    import jwt
except ImportError:
    jwt = None

from sky import global_user_state
from sky.users import token_service


class TestTokenService(unittest.TestCase):
    """Test TokenService class for JWT-based service account tokens."""

    def setUp(self):
        """Set up test fixtures."""
        if jwt is None:
            self.skipTest("PyJWT not available")
            
        # Mock the global_user_state functions
        self.mock_get_system_config = mock.patch.object(
            global_user_state, 'get_system_config'
        ).start()
        self.mock_set_system_config = mock.patch.object(
            global_user_state, 'set_system_config'
        ).start()
        
        # Set up a test secret
        self.test_secret = 'test-secret-key-for-jwt-signing'
        self.mock_get_system_config.return_value = self.test_secret
        
        self.service = token_service.TokenService()

    def tearDown(self):
        """Clean up test fixtures."""
        mock.patch.stopall()

    def test_token_service_initialization(self):
        """Test TokenService initializes correctly."""
        # Should get secret from database
        self.mock_get_system_config.assert_called_once_with('jwt_secret')
        self.assertEqual(self.service.secret_key, self.test_secret)

    def test_token_service_initialization_new_secret(self):
        """Test TokenService generates new secret when none exists."""
        # Mock no existing secret
        self.mock_get_system_config.return_value = None
        
        service = token_service.TokenService()
        
        # Should try to get secret from database
        self.mock_get_system_config.assert_called_with('jwt_secret')
        # Should generate and store new secret
        self.mock_set_system_config.assert_called_once()
        call_args = self.mock_set_system_config.call_args[0]
        self.assertEqual(call_args[0], 'jwt_secret')
        self.assertIsInstance(call_args[1], str)
        self.assertGreater(len(call_args[1]), 20)  # Should be a long secret

    def test_create_token_basic(self):
        """Test basic token creation."""
        result = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token'
        )
        
        # Check return structure
        self.assertIn('token_id', result)
        self.assertIn('token', result)
        self.assertIn('token_hash', result)
        self.assertIn('creator_user_id', result)
        self.assertIn('service_account_user_id', result)
        self.assertIn('token_name', result)
        self.assertIn('created_at', result)
        self.assertIn('expires_at', result)
        
        # Check values
        self.assertEqual(result['creator_user_id'], 'creator123')
        self.assertEqual(result['service_account_user_id'], 'sa-account456')
        self.assertEqual(result['token_name'], 'test-token')
        self.assertIsNone(result['expires_at'])  # No expiration by default
        
        # Check token format
        self.assertTrue(result['token'].startswith('sky_'))
        
        # Check token hash
        expected_hash = hashlib.sha256(result['token'].encode()).hexdigest()
        self.assertEqual(result['token_hash'], expected_hash)

    def test_create_token_with_expiration(self):
        """Test token creation with expiration."""
        expires_in_days = 30
        before_creation = time.time()
        
        result = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token',
            expires_in_days=expires_in_days
        )
        
        after_creation = time.time()
        
        # Check expiration is set correctly (approximately)
        expected_expiration = before_creation + (expires_in_days * 24 * 3600)
        actual_expiration = result['expires_at']
        
        self.assertIsNotNone(actual_expiration)
        self.assertGreaterEqual(actual_expiration, expected_expiration - 5)
        self.assertLessEqual(actual_expiration, 
                           after_creation + (expires_in_days * 24 * 3600) + 5)

    def test_verify_token_valid(self):
        """Test token verification with valid token."""
        # Create a token
        token_data = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token'
        )
        
        # Verify the token
        result = self.service.verify_token(token_data['token'])
        
        self.assertIsNotNone(result)
        if result is not None:
            self.assertEqual(result['sub'], 'sa-account456')
            self.assertEqual(result['token_id'], token_data['token_id'])
            self.assertEqual(result['type'], 'service_account')
            self.assertEqual(result['iss'], 'sky')

    def test_verify_token_with_expiration(self):
        """Test token verification with expiration."""
        # Create a token with short expiration
        token_data = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token',
            expires_in_days=30
        )
        
        # Verify the token (should be valid)
        result = self.service.verify_token(token_data['token'])
        
        self.assertIsNotNone(result)
        if result is not None:
            self.assertEqual(result['sub'], 'sa-account456')
            self.assertIn('exp', result)

    def test_verify_token_invalid_prefix(self):
        """Test token verification fails for invalid prefix."""
        result = self.service.verify_token('invalid_token')
        self.assertIsNone(result)

    def test_verify_token_malformed_jwt(self):
        """Test token verification fails for malformed JWT."""
        result = self.service.verify_token('sky_invalid.jwt.token')
        self.assertIsNone(result)

    def test_verify_token_wrong_secret(self):
        """Test token verification fails for token signed with wrong secret."""
        # Create token with different service
        different_service = token_service.TokenService()
        different_service.secret_key = 'different-secret'
        
        token_data = different_service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token'
        )
        
        # Try to verify with original service
        result = self.service.verify_token(token_data['token'])
        self.assertIsNone(result)

    def test_verify_token_expired(self):
        """Test token verification fails for expired token."""
        # Create token that's already expired by manipulating time
        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
        
        with mock.patch('datetime.datetime') as mock_datetime:
            mock_datetime.now.return_value = past_time
            mock_datetime.timezone = datetime.timezone
            token_data = self.service.create_token(
                creator_user_id='creator123',
                service_account_user_id='sa-account456',
                token_name='test-token',
                expires_in_days=1  # Expired 1 day ago
            )
        
        # Verify token (should fail)
        result = self.service.verify_token(token_data['token'])
        self.assertIsNone(result)

    def test_verify_token_wrong_issuer(self):
        """Test token verification fails for wrong issuer."""
        if jwt is None:
            self.skipTest("PyJWT not available")
            
        # Create a JWT with wrong issuer manually
        payload = {
            'i': 'wrong-issuer',  # Wrong issuer
            't': int(time.time()),
            'u': 'sa-account456',
            'k': 'token123',
            'y': 'sa',
        }
        jwt_token = jwt.encode(payload, self.test_secret, algorithm='HS256')
        full_token = f'sky_{jwt_token}'
        
        result = self.service.verify_token(full_token)
        self.assertIsNone(result)

    def test_verify_token_wrong_type(self):
        """Test token verification fails for wrong token type."""
        if jwt is None:
            self.skipTest("PyJWT not available")
            
        # Create a JWT with wrong type manually
        payload = {
            'i': 'sky',
            't': int(time.time()),
            'u': 'sa-account456',
            'k': 'token123',
            'y': 'wrong-type',  # Wrong type
        }
        jwt_token = jwt.encode(payload, self.test_secret, algorithm='HS256')
        full_token = f'sky_{jwt_token}'
        
        result = self.service.verify_token(full_token)
        self.assertIsNone(result)

    def test_get_token_hash(self):
        """Test token hash generation."""
        token = 'sky_test.jwt.token'
        expected_hash = hashlib.sha256(token.encode()).hexdigest()
        
        result = self.service.get_token_hash(token)
        self.assertEqual(result, expected_hash)

    @mock.patch('secrets.token_urlsafe')
    def test_create_token_deterministic_token_id(self, mock_token_urlsafe):
        """Test that token creation generates deterministic token IDs."""
        mock_token_urlsafe.return_value = 'deterministic-id'
        
        result = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token'
        )
        
        self.assertEqual(result['token_id'], 'deterministic-id')
        mock_token_urlsafe.assert_called_once_with(12)

    def test_jwt_payload_structure(self):
        """Test that JWT payload has the expected compact structure."""
        if jwt is None:
            self.skipTest("PyJWT not available")
            
        token_data = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token',
            expires_in_days=30
        )
        
        # Decode the JWT to check structure
        jwt_token = token_data['token'][4:]  # Remove 'sky_' prefix
        payload = jwt.decode(jwt_token, self.test_secret, algorithms=['HS256'])
        
        # Check compact field names
        self.assertIn('i', payload)  # issuer
        self.assertIn('t', payload)  # issued at
        self.assertIn('u', payload)  # subject (user ID)
        self.assertIn('k', payload)  # token ID
        self.assertIn('y', payload)  # type
        self.assertIn('e', payload)  # expiration
        
        # Check values
        self.assertEqual(payload['i'], 'sky')
        self.assertEqual(payload['u'], 'sa-account456')
        self.assertEqual(payload['y'], 'sa')

    def test_jwt_payload_no_expiration(self):
        """Test JWT payload structure without expiration."""
        if jwt is None:
            self.skipTest("PyJWT not available")
            
        token_data = self.service.create_token(
            creator_user_id='creator123',
            service_account_user_id='sa-account456',
            token_name='test-token'
        )
        
        # Decode the JWT to check structure
        jwt_token = token_data['token'][4:]  # Remove 'sky_' prefix
        payload = jwt.decode(jwt_token, self.test_secret, algorithms=['HS256'])
        
        # Should not have expiration field
        self.assertNotIn('e', payload)


if __name__ == '__main__':
    unittest.main()
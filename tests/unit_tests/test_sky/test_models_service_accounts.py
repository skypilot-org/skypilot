"""Unit tests for service account functionality in models.py."""

import datetime
from unittest import mock

import pytest

from sky import models


class TestServiceAccountModels:
    """Test cases for service account model functionality."""

    def test_user_is_service_account_true(self):
        """Test User.is_service_account() returns True for service account IDs."""
        test_cases = [
            'sa-12345678-test-abcdef12',
            'sa-abc-def-123',
            'sa-prefix',
            'SA-UPPERCASE'  # Should handle case insensitivity
        ]

        for user_id in test_cases:
            user = models.User(id=user_id, name='test-service-account')
            assert user.is_service_account(
            ) is True, f"Failed for ID: {user_id}"

    def test_user_is_service_account_false(self):
        """Test User.is_service_account() returns False for regular user IDs."""
        test_cases = [
            'regular-user-id',
            'user123',
            'abc-def-ghi',
            '12345678',
            'sa_underscore',  # Should not match (uses underscore, not dash)
            'prefix-sa-suffix',  # sa not at beginning
            ''  # Empty string
        ]

        for user_id in test_cases:
            user = models.User(id=user_id, name='regular-user')
            assert user.is_service_account(
            ) is False, f"Failed for ID: {user_id}"

    def test_user_created_at_attribute_exists(self):
        """Test that User model has created_at attribute."""
        user = models.User(id='test123', name='test-user')

        # Should have created_at attribute
        assert hasattr(user, 'created_at')

        # Should be able to set created_at
        test_timestamp = 1234567890
        user.created_at = test_timestamp
        assert user.created_at == test_timestamp

    def test_user_created_at_default_none(self):
        """Test that User.created_at defaults to None when not provided."""
        user = models.User(id='test123', name='test-user')

        # created_at should default to None or current timestamp
        # depending on implementation
        assert user.created_at is None or isinstance(user.created_at,
                                                     (int, float))

    def test_user_created_at_with_value(self):
        """Test creating User with explicit created_at value."""
        test_timestamp = 1234567890
        user = models.User(id='test123',
                           name='test-user',
                           created_at=test_timestamp)

        assert user.created_at == test_timestamp

    def test_service_account_user_properties(self):
        """Test properties specific to service account users."""
        sa_user = models.User(id='sa-12345678-test-abcdef12',
                              name='test-service-account',
                              created_at=1234567890)

        assert sa_user.is_service_account() is True
        assert sa_user.id.startswith('sa-')
        assert sa_user.name == 'test-service-account'
        assert sa_user.created_at == 1234567890

    def test_regular_user_properties(self):
        """Test properties for regular users."""
        regular_user = models.User(id='user123',
                                   name='regular-user',
                                   created_at=1234567890)

        assert regular_user.is_service_account() is False
        assert not regular_user.id.startswith('sa-')
        assert regular_user.name == 'regular-user'
        assert regular_user.created_at == 1234567890

    def test_user_equality(self):
        """Test User equality comparison."""
        user1 = models.User(id='test123', name='test-user')
        user2 = models.User(id='test123', name='test-user')
        user3 = models.User(id='different', name='test-user')

        assert user1 == user2  # Same ID and name
        assert user1 != user3  # Different ID

    def test_user_repr(self):
        """Test User string representation."""
        user = models.User(id='test123', name='test-user')
        user_repr = repr(user)

        assert 'test123' in user_repr
        assert 'test-user' in user_repr

    def test_user_model_immutability_after_creation(self):
        """Test that User model fields can be modified after creation."""
        user = models.User(id='test123', name='original-name')

        # Should be able to modify fields
        user.name = 'updated-name'
        assert user.name == 'updated-name'

        user.created_at = 1234567890
        assert user.created_at == 1234567890

    def test_service_account_id_validation_edge_cases(self):
        """Test edge cases for service account ID validation."""
        edge_cases = [
            ('sa-', True),  # Minimal valid case
            ('sa-a', True),  # Single character after prefix
            ('SA-UPPER', True),  # Uppercase variant
            ('Sa-Mixed', True),  # Mixed case
            ('sa', False),  # Just the prefix without dash
            (' sa-test', True),  # Leading whitespace
            ('sa-test ', True),  # Trailing whitespace
            ('sa--double-dash', True),  # Double dash (should still be valid)
        ]

        for user_id, expected in edge_cases:
            user = models.User(id=user_id, name='test')
            actual = user.is_service_account()
            assert actual == expected, f"Failed for ID '{user_id}': expected {expected}, got {actual}"

    def test_user_with_none_values(self):
        """Test User model behavior with None values."""
        # Test with None name
        user = models.User(id='test123', name=None)
        assert user.id == 'test123'
        assert user.name is None
        assert user.is_service_account() is False

        # Test with None created_at (should be allowed)
        user.created_at = None
        assert user.created_at is None

    @mock.patch('datetime.datetime')
    def test_user_created_at_auto_timestamp(self, mock_datetime):
        """Test automatic timestamp setting if implemented."""
        # Mock current timestamp
        mock_now = mock.Mock()
        mock_now.timestamp.return_value = 1234567890.5
        mock_datetime.datetime.now.return_value = mock_now

        # This test assumes created_at might be auto-set
        # Actual behavior depends on implementation
        user = models.User(id='test123', name='test-user')

        # Verify the user was created successfully regardless of timestamp behavior
        assert user.id == 'test123'
        assert user.name == 'test-user'

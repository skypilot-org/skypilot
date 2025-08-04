"""Unit tests for service account database operations in global_user_state.py."""

import time
from unittest import mock

import pytest

from sky import global_user_state


class TestServiceAccountDatabaseOperations:
    """Test cases for service account database operations."""

    @pytest.fixture
    def mock_engine(self):
        """Mock SQLAlchemy engine."""
        with mock.patch.object(global_user_state,
                               '_SQLALCHEMY_ENGINE') as mock_engine:
            mock_engine.dialect.name = 'sqlite'
            yield mock_engine

    @pytest.fixture
    def mock_session(self, mock_engine):
        """Mock SQLAlchemy session."""
        with mock.patch(
                'sky.global_user_state.orm.Session') as mock_session_class:
            mock_session = mock.Mock()
            mock_session_class.return_value.__enter__.return_value = mock_session
            yield mock_session

    def test_add_service_account_token(self, mock_engine, mock_session):
        """Test adding a service account token."""
        with mock.patch('sky.global_user_state.sqlite') as mock_sqlite:
            with mock.patch('time.time', return_value=1234567890):
                mock_insert = mock.Mock()
                mock_sqlite.insert.return_value = mock_insert

                global_user_state.add_service_account_token(
                    token_id='token123',
                    token_name='test-token',
                    token_hash='hash123',
                    creator_user_hash='user456',
                    service_account_user_id='sa789',
                    expires_at=1234567890 + 2592000  # 30 days
                )

                # Verify insert statement was created with correct values
                mock_sqlite.insert.assert_called_once()
                mock_insert.values.assert_called_once_with(
                    token_id='token123',
                    token_name='test-token',
                    token_hash='hash123',
                    created_at=1234567890,
                    expires_at=1234567890 + 2592000,
                    creator_user_hash='user456',
                    service_account_user_id='sa789')
                mock_session.execute.assert_called_once_with(
                    mock_insert.values.return_value)
                mock_session.commit.assert_called_once()

    def test_add_service_account_token_postgresql(self, mock_engine,
                                                  mock_session):
        """Test adding a service account token with PostgreSQL dialect."""
        mock_engine.dialect.name = 'postgresql'

        with mock.patch('sky.global_user_state.postgresql') as mock_postgresql:
            with mock.patch('time.time', return_value=1234567890):
                mock_insert = mock.Mock()
                mock_postgresql.insert.return_value = mock_insert

                global_user_state.add_service_account_token(
                    token_id='token123',
                    token_name='test-token',
                    token_hash='hash123',
                    creator_user_hash='user456',
                    service_account_user_id='sa789')

                mock_postgresql.insert.assert_called_once()

    def test_add_service_account_token_unsupported_dialect(
            self, mock_engine, mock_session):
        """Test adding a service account token with unsupported database dialect."""
        mock_engine.dialect.name = 'mysql'

        with pytest.raises(ValueError, match='Unsupported database dialect'):
            global_user_state.add_service_account_token(
                token_id='token123',
                token_name='test-token',
                token_hash='hash123',
                creator_user_hash='user456',
                service_account_user_id='sa789')

    def test_get_service_account_token_found(self, mock_engine, mock_session):
        """Test getting a service account token that exists."""
        mock_row = mock.Mock()
        mock_row.token_id = 'token123'
        mock_row.token_name = 'test-token'
        mock_row.token_hash = 'hash123'
        mock_row.created_at = 1234567890
        mock_row.last_used_at = 1234567900
        mock_row.expires_at = 1234567890 + 2592000
        mock_row.creator_user_hash = 'user456'
        mock_row.service_account_user_id = 'sa789'

        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_row

        result = global_user_state.get_service_account_token('token123')

        assert result == {
            'token_id': 'token123',
            'token_name': 'test-token',
            'token_hash': 'hash123',
            'created_at': 1234567890,
            'last_used_at': 1234567900,
            'expires_at': 1234567890 + 2592000,
            'creator_user_hash': 'user456',
            'service_account_user_id': 'sa789'
        }
        mock_session.query.assert_called_once()

    def test_get_service_account_token_not_found(self, mock_engine,
                                                 mock_session):
        """Test getting a service account token that doesn't exist."""
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        result = global_user_state.get_service_account_token('nonexistent')

        assert result is None

    def test_get_all_service_account_tokens(self, mock_engine, mock_session):
        """Test getting all service account tokens."""
        mock_row1 = mock.Mock()
        mock_row1.token_id = 'token1'
        mock_row1.token_name = 'test-token-1'
        mock_row1.token_hash = 'hash1'
        mock_row1.created_at = 1234567890
        mock_row1.last_used_at = None
        mock_row1.expires_at = None
        mock_row1.creator_user_hash = 'user1'
        mock_row1.service_account_user_id = 'sa1'

        mock_row2 = mock.Mock()
        mock_row2.token_id = 'token2'
        mock_row2.token_name = 'test-token-2'
        mock_row2.token_hash = 'hash2'
        mock_row2.created_at = 1234567900
        mock_row2.last_used_at = 1234567910
        mock_row2.expires_at = 1234567890 + 2592000
        mock_row2.creator_user_hash = 'user2'
        mock_row2.service_account_user_id = 'sa2'

        mock_session.query.return_value.all.return_value = [
            mock_row1, mock_row2
        ]

        result = global_user_state.get_all_service_account_tokens()

        assert len(result) == 2
        assert result[0]['token_id'] == 'token1'
        assert result[1]['token_id'] == 'token2'

    def test_update_service_account_token_last_used(self, mock_engine,
                                                    mock_session):
        """Test updating the last_used_at timestamp."""
        with mock.patch('time.time', return_value=1234567999):
            global_user_state.update_service_account_token_last_used('token123')

            mock_session.query.assert_called_once()
            mock_session.query.return_value.filter_by.assert_called_once_with(
                token_id='token123')
            mock_session.query.return_value.filter_by.return_value.update.assert_called_once(
            )
            mock_session.commit.assert_called_once()

    def test_delete_service_account_token_success(self, mock_engine,
                                                  mock_session):
        """Test successfully deleting a service account token."""
        mock_session.query.return_value.filter_by.return_value.delete.return_value = 1

        result = global_user_state.delete_service_account_token('token123')

        assert result is True
        mock_session.query.return_value.filter_by.assert_called_once_with(
            token_id='token123')
        mock_session.commit.assert_called_once()

    def test_delete_service_account_token_not_found(self, mock_engine,
                                                    mock_session):
        """Test deleting a service account token that doesn't exist."""
        mock_session.query.return_value.filter_by.return_value.delete.return_value = 0

        result = global_user_state.delete_service_account_token('nonexistent')

        assert result is False

    def test_rotate_service_account_token_success(self, mock_engine,
                                                  mock_session):
        """Test successfully rotating a service account token."""
        with mock.patch('time.time', return_value=1234568000):
            mock_session.query.return_value.filter_by.return_value.update.return_value = 1

            global_user_state.rotate_service_account_token(
                token_id='token123',
                new_token_hash='new_hash456',
                new_expires_at=1234568000 + 2592000)

            mock_session.query.return_value.filter_by.assert_called_once_with(
                token_id='token123')
            update_call = mock_session.query.return_value.filter_by.return_value.update
            update_call.assert_called_once()

            # Verify the update parameters
            update_args = update_call.call_args[0][0]
            assert 'token_hash' in str(update_args)
            assert 'expires_at' in str(update_args)
            assert 'last_used_at' in str(update_args)
            assert 'created_at' in str(update_args)

            mock_session.commit.assert_called_once()

    def test_rotate_service_account_token_not_found(self, mock_engine,
                                                    mock_session):
        """Test rotating a service account token that doesn't exist."""
        with mock.patch('time.time', return_value=1234568000):
            mock_session.query.return_value.filter_by.return_value.update.return_value = 0

            with pytest.raises(
                    ValueError,
                    match='Service account token token123 not found'):
                global_user_state.rotate_service_account_token(
                    token_id='token123', new_token_hash='new_hash456')

    def test_rotate_service_account_token_without_expiration(
            self, mock_engine, mock_session):
        """Test rotating a service account token without setting expiration."""
        with mock.patch('time.time', return_value=1234568000):
            mock_session.query.return_value.filter_by.return_value.update.return_value = 1

            global_user_state.rotate_service_account_token(
                token_id='token123',
                new_token_hash='new_hash456',
                new_expires_at=None)

            update_call = mock_session.query.return_value.filter_by.return_value.update
            update_call.assert_called_once()

    def test_get_service_account_tokens_by_sa_user_id_multiple_tokens(
            self, mock_engine, mock_session):
        """Test getting multiple service account tokens for a service account."""
        mock_row1 = mock.Mock()
        mock_row1.token_id = 'token1'
        mock_row1.token_name = 'test-token-1'
        mock_row1.token_hash = 'hash1'
        mock_row1.created_at = 1234567890
        mock_row1.last_used_at = 1234567900
        mock_row1.expires_at = 1234567890 + 2592000
        mock_row1.creator_user_hash = 'creator1'
        mock_row1.service_account_user_id = 'sa123'

        mock_row2 = mock.Mock()
        mock_row2.token_id = 'token2'
        mock_row2.token_name = 'test-token-2'
        mock_row2.token_hash = 'hash2'
        mock_row2.created_at = 1234567900
        mock_row2.last_used_at = None
        mock_row2.expires_at = None
        mock_row2.creator_user_hash = 'creator2'
        mock_row2.service_account_user_id = 'sa123'

        mock_session.query.return_value.filter_by.return_value.all.return_value = [
            mock_row1, mock_row2
        ]

        result = global_user_state.get_service_account_tokens_by_sa_user_id(
            'sa123')

        assert len(result) == 2
        assert result[0] == {
            'token_id': 'token1',
            'token_name': 'test-token-1',
            'token_hash': 'hash1',
            'created_at': 1234567890,
            'last_used_at': 1234567900,
            'expires_at': 1234567890 + 2592000,
            'creator_user_hash': 'creator1',
            'service_account_user_id': 'sa123'
        }
        assert result[1] == {
            'token_id': 'token2',
            'token_name': 'test-token-2',
            'token_hash': 'hash2',
            'created_at': 1234567900,
            'last_used_at': None,
            'expires_at': None,
            'creator_user_hash': 'creator2',
            'service_account_user_id': 'sa123'
        }

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter_by.assert_called_once_with(
            service_account_user_id='sa123')

    def test_get_service_account_tokens_by_sa_user_id_single_token(
            self, mock_engine, mock_session):
        """Test getting a single service account token for a service account."""
        mock_row = mock.Mock()
        mock_row.token_id = 'token1'
        mock_row.token_name = 'test-token'
        mock_row.token_hash = 'hash1'
        mock_row.created_at = 1234567890
        mock_row.last_used_at = 1234567900
        mock_row.expires_at = 1234567890 + 2592000
        mock_row.creator_user_hash = 'creator1'
        mock_row.service_account_user_id = 'sa456'

        mock_session.query.return_value.filter_by.return_value.all.return_value = [
            mock_row
        ]

        result = global_user_state.get_service_account_tokens_by_sa_user_id(
            'sa456')

        assert len(result) == 1
        assert result[0] == {
            'token_id': 'token1',
            'token_name': 'test-token',
            'token_hash': 'hash1',
            'created_at': 1234567890,
            'last_used_at': 1234567900,
            'expires_at': 1234567890 + 2592000,
            'creator_user_hash': 'creator1',
            'service_account_user_id': 'sa456'
        }

        mock_session.query.return_value.filter_by.assert_called_once_with(
            service_account_user_id='sa456')

    def test_get_service_account_tokens_by_sa_user_id_no_tokens(
            self, mock_engine, mock_session):
        """Test getting service account tokens when none exist for the service account."""
        mock_session.query.return_value.filter_by.return_value.all.return_value = []

        result = global_user_state.get_service_account_tokens_by_sa_user_id(
            'nonexistent_sa')

        assert result == []
        mock_session.query.return_value.filter_by.assert_called_once_with(
            service_account_user_id='nonexistent_sa')

    def test_function_decorators_present(self):
        """Test that all service account functions have proper attributes."""
        # This test ensures that all functions exist and are callable
        functions_to_test = [
            global_user_state.add_service_account_token,
            global_user_state.get_service_account_token,
            global_user_state.get_all_service_account_tokens,
            global_user_state.get_service_account_tokens_by_sa_user_id,
            global_user_state.update_service_account_token_last_used,
            global_user_state.delete_service_account_token,
            global_user_state.rotate_service_account_token,
        ]

        # Verify all functions exist and are callable
        for func in functions_to_test:
            assert callable(func), f"Function {func.__name__} is not callable"
            # Verify the function has a proper name and module
            assert hasattr(
                func, '__name__'), f"Function {func} has no __name__ attribute"
            assert hasattr(
                func,
                '__module__'), f"Function {func} has no __module__ attribute"

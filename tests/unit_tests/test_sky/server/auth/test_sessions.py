"""Tests for the auth sessions module."""
import hashlib
import time
from unittest import mock

import pytest

from sky.server.auth import sessions
from sky.utils import common_utils


class TestAuthSession:
    """Tests for the AuthSession class."""

    def test_session_creation(self):
        """Test that a session can be created."""
        code_challenge = 'test_challenge_123'
        session = sessions.AuthSession('session_id_123', code_challenge)

        assert session.session_id == 'session_id_123'
        assert session.code_challenge == code_challenge
        assert session.status == 'pending'
        assert session.token is None
        assert not session.is_expired()

    def test_session_expiration(self):
        """Test that sessions expire correctly."""
        session = sessions.AuthSession('session_id', 'challenge')

        # Session should not be expired immediately
        assert not session.is_expired()

        # Mock the created_at to be in the past
        session.created_at = time.time(
        ) - sessions.SESSION_EXPIRATION_SECONDS - 1
        assert session.is_expired()

    def test_verify_code_verifier_valid(self):
        """Test that a valid code verifier is verified correctly."""
        # Generate a proper code verifier and challenge
        code_verifier = 'test_code_verifier_that_is_sufficiently_long'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = sessions.AuthSession('session_id', code_challenge)

        assert session.verify_code_verifier(code_verifier)

    def test_verify_code_verifier_invalid(self):
        """Test that an invalid code verifier fails verification."""
        code_verifier = 'correct_verifier'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = sessions.AuthSession('session_id', code_challenge)

        assert not session.verify_code_verifier('wrong_verifier')


class TestAuthSessionStore:
    """Tests for the AuthSessionStore class."""

    @pytest.fixture
    def store(self):
        """Create a fresh session store for each test."""
        return sessions.AuthSessionStore()

    def test_create_session(self, store):
        """Test creating a new session."""
        code_challenge = 'test_challenge'
        session = store.create_session(code_challenge)

        assert session.session_id is not None
        assert len(session.session_id) > 0
        assert session.code_challenge == code_challenge
        assert session.status == 'pending'

    def test_get_session(self, store):
        """Test retrieving a session."""
        session = store.create_session('challenge')
        retrieved = store.get_session(session.session_id)

        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_session(self, store):
        """Test that getting a nonexistent session returns None."""
        assert store.get_session('nonexistent') is None

    def test_get_expired_session(self, store):
        """Test that expired sessions are cleaned up."""
        session = store.create_session('challenge')

        # Expire the session
        session.created_at = time.time(
        ) - sessions.SESSION_EXPIRATION_SECONDS - 1

        assert store.get_session(session.session_id) is None

    def test_authorize_session(self, store):
        """Test authorizing a session."""
        session = store.create_session('challenge')
        token = 'test_token_123'

        assert store.authorize_session(session.session_id, token)

        # Verify the session is now authorized
        retrieved = store.get_session(session.session_id)
        assert retrieved.status == 'authorized'
        assert retrieved.token == token

    def test_authorize_nonexistent_session(self, store):
        """Test that authorizing a nonexistent session fails."""
        assert not store.authorize_session('nonexistent', 'token')

    def test_authorize_already_authorized_session(self, store):
        """Test that authorizing an already authorized session fails."""
        session = store.create_session('challenge')
        store.authorize_session(session.session_id, 'token1')

        # Try to authorize again
        assert not store.authorize_session(session.session_id, 'token2')

    def test_consume_session(self, store):
        """Test consuming an authorized session."""
        # Create a valid code verifier and challenge
        code_verifier = 'test_verifier_123456'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)
        token = 'test_token'
        store.authorize_session(session.session_id, token)

        # Consume the session
        retrieved_token = store.consume_session(session.session_id,
                                                code_verifier)
        assert retrieved_token == token

        # Session should be deleted after consumption
        assert store.get_session(session.session_id) is None

    def test_consume_pending_session(self, store):
        """Test that consuming a pending session returns None."""
        code_verifier = 'test_verifier'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)

        # Try to consume without authorizing first
        assert store.consume_session(session.session_id, code_verifier) is None

        # Session should still exist (not deleted)
        assert store.get_session(session.session_id) is not None

    def test_consume_with_wrong_verifier(self, store):
        """Test that consuming with wrong verifier fails."""
        code_verifier = 'correct_verifier'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)
        store.authorize_session(session.session_id, 'token')

        # Try to consume with wrong verifier
        assert store.consume_session(session.session_id,
                                     'wrong_verifier') is None

        # Session should still exist (not consumed)
        assert store.get_session(session.session_id) is not None

    def test_get_session_status(self, store):
        """Test getting session status."""
        code_verifier = 'test_verifier'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)

        # Check pending status
        status = store.get_session_status(session.session_id, code_verifier)
        assert status == 'pending'

        # Authorize and check status
        store.authorize_session(session.session_id, 'token')
        status = store.get_session_status(session.session_id, code_verifier)
        assert status == 'authorized'

    def test_get_session_status_wrong_verifier(self, store):
        """Test that status check with wrong verifier returns None."""
        code_verifier = 'correct_verifier'
        verifier_hash = hashlib.sha256(
            code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        store.create_session(code_challenge)

        # Wrong verifier should return None
        status = store.get_session_status('session_id', 'wrong_verifier')
        assert status is None

    def test_expired_sessions_cleanup(self, store):
        """Test that expired sessions are cleaned up on create."""
        # Create a session and expire it
        session1 = store.create_session('challenge1')
        session1.created_at = time.time(
        ) - sessions.SESSION_EXPIRATION_SECONDS - 1

        # Create another session - this should trigger cleanup
        session2 = store.create_session('challenge2')

        # The expired session should be cleaned up
        assert store.get_session(session1.session_id) is None
        assert store.get_session(session2.session_id) is not None


class TestGlobalStore:
    """Tests for the global auth_session_store instance."""

    def test_global_store_exists(self):
        """Test that the global store is available."""
        assert sessions.auth_session_store is not None
        assert isinstance(sessions.auth_session_store,
                          sessions.AuthSessionStore)

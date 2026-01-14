"""Tests for the auth sessions module."""
import hashlib
import time

import pytest

from sky.server.auth import sessions
from sky.utils import common_utils


class TestAuthSession:
    """Tests for the AuthSession class."""

    def test_session_creation(self):
        session = sessions.AuthSession('session_id', 'challenge')
        assert session.session_id == 'session_id'
        assert session.status == 'pending'
        assert session.token is None
        assert not session.is_expired()

    def test_session_expiration(self):
        session = sessions.AuthSession('session_id', 'challenge')
        assert not session.is_expired()
        session.created_at = time.time() - sessions.SESSION_EXPIRATION_SECONDS - 1
        assert session.is_expired()

    def test_verify_code_verifier(self):
        code_verifier = 'test_verifier_123456'
        verifier_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = sessions.AuthSession('session_id', code_challenge)
        assert session.verify_code_verifier(code_verifier)
        assert not session.verify_code_verifier('wrong_verifier')


class TestAuthSessionStore:
    """Tests for the AuthSessionStore class."""

    @pytest.fixture
    def store(self):
        return sessions.AuthSessionStore()

    def test_create_session(self, store):
        session = store.create_session('challenge')
        assert session.session_id
        assert session.status == 'pending'

    def test_get_session(self, store):
        session = store.create_session('challenge')
        retrieved = store.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_session(self, store):
        assert store.get_session('nonexistent') is None

    def test_get_expired_session(self, store):
        session = store.create_session('challenge')
        session.created_at = time.time() - sessions.SESSION_EXPIRATION_SECONDS - 1
        assert store.get_session(session.session_id) is None

    def test_authorize_session(self, store):
        session = store.create_session('challenge')
        assert store.authorize_session(session.session_id, 'token123')

        retrieved = store.get_session(session.session_id)
        assert retrieved.status == 'authorized'
        assert retrieved.token == 'token123'

    def test_authorize_nonexistent_session(self, store):
        assert not store.authorize_session('nonexistent', 'token')

    def test_authorize_twice_fails(self, store):
        session = store.create_session('challenge')
        assert store.authorize_session(session.session_id, 'token1')
        assert not store.authorize_session(session.session_id, 'token2')

    def test_poll_session_authorized(self, store):
        code_verifier = 'test_verifier'
        verifier_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)
        store.authorize_session(session.session_id, 'my_token')

        status, token = store.poll_session(session.session_id, code_verifier)
        assert status == 'authorized'
        assert token == 'my_token'

        # Session should be consumed (deleted)
        assert store.get_session(session.session_id) is None

    def test_poll_session_pending(self, store):
        code_verifier = 'test_verifier'
        verifier_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)

        status, token = store.poll_session(session.session_id, code_verifier)
        assert status == 'pending'
        assert token is None

        # Session should still exist
        assert store.get_session(session.session_id) is not None

    def test_poll_session_wrong_verifier(self, store):
        code_verifier = 'correct_verifier'
        verifier_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = common_utils.base64_url_encode(verifier_hash)

        session = store.create_session(code_challenge)
        store.authorize_session(session.session_id, 'token')

        status, token = store.poll_session(session.session_id, 'wrong_verifier')
        assert status is None
        assert token is None

        # Session should still exist (not consumed)
        assert store.get_session(session.session_id) is not None

    def test_poll_session_not_found(self, store):
        status, token = store.poll_session('nonexistent', 'verifier')
        assert status is None
        assert token is None

    def test_expired_sessions_cleanup(self, store):
        session1 = store.create_session('challenge1')
        session1.created_at = time.time() - sessions.SESSION_EXPIRATION_SECONDS - 1

        session2 = store.create_session('challenge2')

        assert store.get_session(session1.session_id) is None
        assert store.get_session(session2.session_id) is not None


class TestGlobalStore:
    def test_global_store_exists(self):
        assert isinstance(sessions.auth_session_store, sessions.AuthSessionStore)

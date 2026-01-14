"""Tests for the auth sessions module."""
import hashlib
import time

import pytest

from sky.server.auth import sessions
from sky.utils import common_utils


class TestAuthSession:
    """Tests for the AuthSession class."""

    def test_session_creation(self):
        session = sessions.AuthSession('challenge')
        assert session.code_challenge == 'challenge'
        assert session.status == 'pending'
        assert session.token is None
        assert not session.is_expired()

    def test_session_expiration(self):
        session = sessions.AuthSession('challenge')
        assert not session.is_expired()
        session.created_at = time.time(
        ) - sessions.SESSION_EXPIRATION_SECONDS - 1
        assert session.is_expired()


class TestComputeChallenge:
    """Tests for the compute_challenge function."""

    def test_compute_challenge(self):
        code_verifier = 'test_verifier_123456'
        verifier_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        expected = common_utils.base64_url_encode(verifier_hash)
        assert sessions.compute_challenge(code_verifier) == expected

    def test_different_verifiers_different_challenges(self):
        challenge1 = sessions.compute_challenge('verifier1')
        challenge2 = sessions.compute_challenge('verifier2')
        assert challenge1 != challenge2


class TestAuthSessionStore:
    """Tests for the AuthSessionStore class."""

    @pytest.fixture
    def store(self):
        return sessions.AuthSessionStore()

    def test_get_or_create_session(self, store):
        session = store.get_or_create_session('challenge')
        assert session.code_challenge == 'challenge'
        assert session.status == 'pending'

    def test_get_or_create_returns_existing(self, store):
        session1 = store.get_or_create_session('challenge')
        session2 = store.get_or_create_session('challenge')
        assert session1 is session2

    def test_get_session(self, store):
        store.get_or_create_session('challenge')
        retrieved = store.get_session('challenge')
        assert retrieved is not None
        assert retrieved.code_challenge == 'challenge'

    def test_get_nonexistent_session(self, store):
        assert store.get_session('nonexistent') is None

    def test_get_expired_session(self, store):
        session = store.get_or_create_session('challenge')
        session.created_at = time.time(
        ) - sessions.SESSION_EXPIRATION_SECONDS - 1
        assert store.get_session('challenge') is None

    def test_authorize_session(self, store):
        store.get_or_create_session('challenge')
        assert store.authorize_session('challenge', 'token123')

        retrieved = store.get_session('challenge')
        assert retrieved.status == 'authorized'
        assert retrieved.token == 'token123'

    def test_authorize_nonexistent_session(self, store):
        assert not store.authorize_session('nonexistent', 'token')

    def test_authorize_twice_fails(self, store):
        store.get_or_create_session('challenge')
        assert store.authorize_session('challenge', 'token1')
        assert not store.authorize_session('challenge', 'token2')

    def test_poll_session_authorized(self, store):
        code_verifier = 'test_verifier'
        code_challenge = sessions.compute_challenge(code_verifier)

        store.get_or_create_session(code_challenge)
        store.authorize_session(code_challenge, 'my_token')

        status, token = store.poll_session(code_verifier)
        assert status == 'authorized'
        assert token == 'my_token'

        # Session should be consumed (deleted)
        assert store.get_session(code_challenge) is None

    def test_poll_session_pending(self, store):
        code_verifier = 'test_verifier'
        code_challenge = sessions.compute_challenge(code_verifier)

        store.get_or_create_session(code_challenge)

        status, token = store.poll_session(code_verifier)
        assert status == 'pending'
        assert token is None

        # Session should still exist
        assert store.get_session(code_challenge) is not None

    def test_poll_session_not_found(self, store):
        # Poll with a verifier that has no corresponding session
        status, token = store.poll_session('nonexistent_verifier')
        assert status is None
        assert token is None

    def test_expired_sessions_cleanup(self, store):
        session1 = store.get_or_create_session('challenge1')
        session1.created_at = time.time(
        ) - sessions.SESSION_EXPIRATION_SECONDS - 1

        store.get_or_create_session('challenge2')

        assert store.get_session('challenge1') is None
        assert store.get_session('challenge2') is not None


class TestGlobalStore:

    def test_global_store_exists(self):
        assert isinstance(sessions.auth_session_store,
                          sessions.AuthSessionStore)

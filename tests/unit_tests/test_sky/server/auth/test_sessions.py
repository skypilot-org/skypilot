"""Tests for the auth sessions module."""
import hashlib
import time

import pytest

from sky.server import constants as server_constants
from sky.server.auth import sessions
from sky.utils import common_utils


class TestComputeCodeChallenge:
    """Tests for the compute_code_challenge function."""

    def test_compute_challenge(self):
        code_verifier = 'test_verifier_123456'
        verifier_hash = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        expected = common_utils.base64_url_encode(verifier_hash)
        assert common_utils.compute_code_challenge(code_verifier) == expected

    def test_different_verifiers_different_challenges(self):
        challenge1 = common_utils.compute_code_challenge('verifier1')
        challenge2 = common_utils.compute_code_challenge('verifier2')
        assert challenge1 != challenge2


class TestAuthSessionStore:
    """Tests for the AuthSessionStore class."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a store with a temporary database."""
        db_path = str(tmp_path / 'test_sessions.db')
        store = sessions.AuthSessionStore()
        store._db_path = db_path  # pylint: disable=protected-access
        return store

    def test_create_session(self, store):
        code_verifier = 'test_verifier'
        code_challenge = common_utils.compute_code_challenge(code_verifier)

        store.create_session(code_challenge, 'my_token')

        token = store.poll_session(code_verifier)
        assert token == 'my_token'

    def test_create_session_overwrites(self, store):
        # Duplicate authorize clicks should just overwrite
        code_verifier = 'test_verifier'
        code_challenge = common_utils.compute_code_challenge(code_verifier)

        store.create_session(code_challenge, 'token1')
        store.create_session(code_challenge, 'token2')

        token = store.poll_session(code_verifier)
        assert token == 'token2'

    def test_poll_session_consumes(self, store):
        code_verifier = 'test_verifier'
        code_challenge = common_utils.compute_code_challenge(code_verifier)

        store.create_session(code_challenge, 'my_token')

        # First poll returns the token
        token = store.poll_session(code_verifier)
        assert token == 'my_token'

        # Session should be consumed (deleted)
        token = store.poll_session(code_verifier)
        assert token is None

    def test_poll_session_not_found(self, store):
        # Poll with a verifier that has no corresponding session
        token = store.poll_session('nonexistent_verifier')
        assert token is None

    def test_poll_expired_session(self, store, monkeypatch):
        code_verifier = 'test_verifier'
        code_challenge = common_utils.compute_code_challenge(code_verifier)

        store.create_session(code_challenge, 'my_token')

        # Fast-forward time past expiration
        future_time = time.time(
        ) + server_constants.AUTH_SESSION_TIMEOUT_SECONDS + 10
        monkeypatch.setattr(time, 'time', lambda: future_time)

        # Should return None due to expiration
        token = store.poll_session(code_verifier)
        assert token is None

    def test_expired_sessions_cleanup(self, store, monkeypatch):
        code_verifier1 = 'verifier1'
        code_verifier2 = 'verifier2'
        code_challenge1 = common_utils.compute_code_challenge(code_verifier1)
        code_challenge2 = common_utils.compute_code_challenge(code_verifier2)

        store.create_session(code_challenge1, 'token1')
        store.create_session(code_challenge2, 'token2')

        # Fast-forward time past expiration
        future_time = time.time(
        ) + server_constants.AUTH_SESSION_TIMEOUT_SECONDS + 10
        monkeypatch.setattr(time, 'time', lambda: future_time)

        # Both should be expired now
        assert store.poll_session(code_verifier1) is None
        assert store.poll_session(code_verifier2) is None


class TestGlobalStore:

    def test_global_store_exists(self):
        assert isinstance(sessions.auth_session_store,
                          sessions.AuthSessionStore)

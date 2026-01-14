"""In-memory auth session storage for CLI login flow.

This module provides server-side session storage for the PKCE-based
CLI authentication flow. Sessions are stored in memory and expire
after a configurable timeout.
"""
import hashlib
import secrets
import threading
import time
from typing import Dict, Optional

from sky.utils import common_utils

# Session expiration time in seconds (5 minutes)
SESSION_EXPIRATION_SECONDS = 300


class AuthSession:
    """Represents an authentication session."""

    def __init__(self, session_id: str, code_challenge: str):
        self.session_id = session_id
        self.code_challenge = code_challenge
        self.status = 'pending'  # 'pending', 'authorized'
        self.token: Optional[str] = None
        self.created_at = time.time()

    def is_expired(self) -> bool:
        """Check if the session has expired."""
        return time.time() - self.created_at > SESSION_EXPIRATION_SECONDS

    def verify_code_verifier(self, code_verifier: str) -> bool:
        """Verify the code verifier against the stored code challenge.

        Uses SHA256 hash comparison (S256 method from PKCE).
        """
        # Compute SHA256 hash of the code verifier
        digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        # Base64url encode (no padding)
        computed_challenge = common_utils.base64_url_encode(digest)
        return secrets.compare_digest(computed_challenge, self.code_challenge)


class AuthSessionStore:
    """Thread-safe in-memory storage for auth sessions."""

    def __init__(self):
        self._sessions: Dict[str, AuthSession] = {}
        self._lock = threading.Lock()

    def create_session(self, code_challenge: str) -> AuthSession:
        """Create a new auth session.

        Args:
            code_challenge: Base64url-encoded SHA256 hash of the code verifier.

        Returns:
            The created AuthSession.
        """
        session_id = secrets.token_urlsafe(32)

        session = AuthSession(session_id, code_challenge)

        with self._lock:
            # Clean up expired sessions while holding the lock
            self._cleanup_expired_sessions_locked()
            self._sessions[session_id] = session

        return session

    def get_session(self, session_id: str) -> Optional[AuthSession]:
        """Get a session by ID.

        Returns None if the session doesn't exist or has expired.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired():
                del self._sessions[session_id]
                return None
            return session

    def authorize_session(self, session_id: str, token: str) -> bool:
        """Mark a session as authorized and store the token.

        Args:
            session_id: The session ID to authorize.
            token: The base64-encoded token to store.

        Returns:
            True if successful, False if session not found or expired.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.is_expired():
                if session is not None:
                    del self._sessions[session_id]
                return False

            if session.status != 'pending':
                # Already authorized or in an invalid state
                return False

            session.status = 'authorized'
            session.token = token
            return True

    def consume_session(self, session_id: str,
                        code_verifier: str) -> Optional[str]:
        """Consume a session and return the token if valid.

        This verifies the code verifier, returns the token if the session
        is authorized, and deletes the session (single-use).

        Args:
            session_id: The session ID.
            code_verifier: The code verifier to validate against the challenge.

        Returns:
            The token if valid and authorized, None otherwise.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None

            if session.is_expired():
                del self._sessions[session_id]
                return None

            # Verify the code verifier
            if not session.verify_code_verifier(code_verifier):
                return None

            if session.status != 'authorized' or session.token is None:
                # Session exists and verifier is valid, but not yet authorized
                # Return None but don't delete - client should keep polling
                return None

            # Success - consume the session (single-use)
            token = session.token
            del self._sessions[session_id]
            return token

    def get_session_status(self, session_id: str,
                           code_verifier: str) -> Optional[str]:
        """Get the status of a session after verifying the code verifier.

        Args:
            session_id: The session ID.
            code_verifier: The code verifier to validate.

        Returns:
            'pending', 'authorized', or None if session not found/invalid.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None

            if session.is_expired():
                del self._sessions[session_id]
                return None

            # Verify the code verifier
            if not session.verify_code_verifier(code_verifier):
                return None

            return session.status

    def _cleanup_expired_sessions_locked(self) -> None:
        """Remove expired sessions. Must be called while holding _lock."""
        expired_ids = [
            sid for sid, session in self._sessions.items()
            if session.is_expired()
        ]
        for sid in expired_ids:
            del self._sessions[sid]


# Global session store instance
auth_session_store = AuthSessionStore()

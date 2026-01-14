"""In-memory auth session storage for CLI login flow.

This module provides server-side session storage for the PKCE-based
CLI authentication flow. Sessions are keyed by code_challenge and
expire after a configurable timeout.
"""
import hashlib
import threading
import time
from typing import Dict, Optional, Tuple

from sky.utils import common_utils

# Session expiration time in seconds (5 minutes)
SESSION_EXPIRATION_SECONDS = 300


class AuthSession:
    """Represents an authentication session."""

    def __init__(self, code_challenge: str):
        self.code_challenge = code_challenge
        self.status = 'pending'  # 'pending' or 'authorized'
        self.token: Optional[str] = None
        self.created_at = time.time()

    def is_expired(self) -> bool:
        return time.time() - self.created_at > SESSION_EXPIRATION_SECONDS


def compute_challenge(code_verifier: str) -> str:
    """Compute code_challenge from code_verifier using S256."""
    digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    return common_utils.base64_url_encode(digest)


class AuthSessionStore:
    """Thread-safe in-memory storage for auth sessions."""

    def __init__(self):
        self._sessions: Dict[str, AuthSession] = {}
        self._lock = threading.Lock()

    def get_or_create_session(self, code_challenge: str) -> AuthSession:
        """Get or create a session for the given code_challenge."""
        with self._lock:
            self._cleanup_expired_sessions_locked()

            session = self._sessions.get(code_challenge)
            if session is not None and not session.is_expired():
                return session

            # Create new session
            session = AuthSession(code_challenge)
            self._sessions[code_challenge] = session
            return session

    def get_session(self, code_challenge: str) -> Optional[AuthSession]:
        """Get session by code_challenge. Returns None if not found/expired."""
        with self._lock:
            session = self._sessions.get(code_challenge)
            if session is None:
                return None
            if session.is_expired():
                del self._sessions[code_challenge]
                return None
            return session

    def authorize_session(self, code_challenge: str, token: str) -> bool:
        """Mark a session as authorized and store the token."""
        with self._lock:
            session = self._sessions.get(code_challenge)
            if session is None or session.is_expired():
                if session is not None:
                    del self._sessions[code_challenge]
                return False

            if session.status != 'pending':
                return False

            session.status = 'authorized'
            session.token = token
            return True

    def poll_session(self,
                     code_verifier: str) -> Tuple[Optional[str], Optional[str]]:
        """Poll a session for its token using code_verifier.

        Computes code_challenge from code_verifier to look up the session.

        Returns:
            (status, token) tuple where:
            - ('authorized', token) - Success, session consumed
            - ('pending', None) - Valid but not yet authorized
            - (None, None) - Not found or expired
        """
        code_challenge = compute_challenge(code_verifier)

        with self._lock:
            session = self._sessions.get(code_challenge)
            if session is None:
                return (None, None)

            if session.is_expired():
                del self._sessions[code_challenge]
                return (None, None)

            if session.status == 'pending':
                return ('pending', None)

            # Authorized - consume and return token
            token = session.token
            del self._sessions[code_challenge]
            return ('authorized', token)

    def _cleanup_expired_sessions_locked(self) -> None:
        """Remove expired sessions. Must hold _lock."""
        expired = [c for c, sess in self._sessions.items() if sess.is_expired()]
        for c in expired:
            del self._sessions[c]


# Global session store instance
auth_session_store = AuthSessionStore()

"""In-memory auth session storage for CLI login flow.

This module provides server-side session storage for the PKCE-based
CLI authentication flow. Sessions are stored in memory and expire
after a configurable timeout.
"""
import hashlib
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from sky.utils import common_utils

# Session expiration time in seconds (5 minutes)
SESSION_EXPIRATION_SECONDS = 300


class AuthSession:
    """Represents an authentication session."""

    def __init__(self, session_id: str, code_challenge: str):
        self.session_id = session_id
        self.code_challenge = code_challenge
        self.status = 'pending'  # 'pending' or 'authorized'
        self.token: Optional[str] = None
        self.created_at = time.time()

    def is_expired(self) -> bool:
        return time.time() - self.created_at > SESSION_EXPIRATION_SECONDS

    def verify_code_verifier(self, code_verifier: str) -> bool:
        """Verify code verifier against stored challenge using S256."""
        digest = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        computed_challenge = common_utils.base64_url_encode(digest)
        return secrets.compare_digest(computed_challenge, self.code_challenge)


class AuthSessionStore:
    """Thread-safe in-memory storage for auth sessions."""

    def __init__(self):
        self._sessions: Dict[str, AuthSession] = {}
        self._lock = threading.Lock()

    def create_session(self, code_challenge: str) -> AuthSession:
        """Create a new auth session."""
        session_id = secrets.token_urlsafe(32)
        session = AuthSession(session_id, code_challenge)

        with self._lock:
            self._cleanup_expired_sessions_locked()
            self._sessions[session_id] = session

        return session

    def get_session(self, session_id: str) -> Optional[AuthSession]:
        """Get a session by ID. Returns None if not found or expired."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired():
                del self._sessions[session_id]
                return None
            return session

    def authorize_session(self, session_id: str, token: str) -> bool:
        """Mark a session as authorized and store the token."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.is_expired():
                if session is not None:
                    del self._sessions[session_id]
                return False

            if session.status != 'pending':
                return False

            session.status = 'authorized'
            session.token = token
            return True

    def poll_session(self, session_id: str,
                     code_verifier: str) -> Tuple[Optional[str], Optional[str]]:
        """Poll a session for its token.

        Returns:
            (status, token) tuple where:
            - ('authorized', token) - Success, session consumed
            - ('pending', None) - Valid but not yet authorized
            - (None, None) - Not found, expired, or invalid verifier
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return (None, None)

            if session.is_expired():
                del self._sessions[session_id]
                return (None, None)

            if not session.verify_code_verifier(code_verifier):
                return (None, None)

            if session.status == 'pending':
                return ('pending', None)

            # Authorized - consume and return token
            token = session.token
            del self._sessions[session_id]
            return ('authorized', token)

    def _cleanup_expired_sessions_locked(self) -> None:
        """Remove expired sessions. Must hold _lock."""
        expired = [s for s, sess in self._sessions.items() if sess.is_expired()]
        for s in expired:
            del self._sessions[s]


# Global session store instance
auth_session_store = AuthSessionStore()

"""SQLite-based auth session storage for CLI login flow.

This module provides server-side session storage for the polling-based
CLI authentication flow. Sessions are keyed by code_challenge and
expire after a configurable timeout. Uses SQLite for cross-worker access.
"""
import os
import sqlite3
import time
from typing import Optional, Tuple

from sky.server import constants as server_constants
from sky.utils import common_utils
from sky.utils.db import db_utils

# Session expiration time in seconds (5 minutes)
SESSION_EXPIRATION_SECONDS = 300

# Table name for auth sessions
_AUTH_SESSIONS_TABLE = 'auth_sessions'


class AuthSession:
    """Represents an authentication session."""

    def __init__(self, code_challenge: str, status: str, token: Optional[str],
                 created_at: float):
        self.code_challenge = code_challenge
        self.status = status  # 'pending' or 'authorized'
        self.token = token
        self.created_at = created_at

    def is_expired(self) -> bool:
        return time.time() - self.created_at > SESSION_EXPIRATION_SECONDS


class AuthSessionStore:
    """SQLite-backed storage for auth sessions."""

    def __init__(self):
        self._db_path = os.path.expanduser(
            server_constants.API_SERVER_REQUEST_DB_PATH)

    def _get_cursor(self):
        """Get a cursor to the database, creating table if needed."""
        return db_utils.safe_cursor(self._db_path)

    def _ensure_table(self, cursor: sqlite3.Cursor) -> None:
        """Ensure the auth_sessions table exists."""
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {_AUTH_SESSIONS_TABLE} (
                code_challenge TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                token TEXT,
                created_at REAL NOT NULL
            )
        """)

    def _cleanup_expired(self, cursor: sqlite3.Cursor) -> None:
        """Remove expired sessions."""
        expiry_time = time.time() - SESSION_EXPIRATION_SECONDS
        cursor.execute(
            f'DELETE FROM {_AUTH_SESSIONS_TABLE} WHERE created_at < ?',
            (expiry_time,))

    def get_or_create_session(self, code_challenge: str) -> AuthSession:
        """Get or create a session for the given code_challenge."""
        with self._get_cursor() as cursor:
            self._ensure_table(cursor)
            self._cleanup_expired(cursor)

            # Try to get existing session
            cursor.execute(
                f'SELECT status, token, created_at FROM {_AUTH_SESSIONS_TABLE} '
                f'WHERE code_challenge = ?', (code_challenge,))
            row = cursor.fetchone()

            if row is not None:
                return AuthSession(code_challenge, row[0], row[1], row[2])

            # Create new session
            created_at = time.time()
            cursor.execute(
                f'INSERT INTO {_AUTH_SESSIONS_TABLE} '
                '(code_challenge, status, token, created_at) '
                'VALUES (?, ?, ?, ?)',
                (code_challenge, 'pending', None, created_at))
            return AuthSession(code_challenge, 'pending', None, created_at)

    def get_session(self, code_challenge: str) -> Optional[AuthSession]:
        """Get session by code_challenge. Returns None if not found/expired."""
        with self._get_cursor() as cursor:
            self._ensure_table(cursor)

            cursor.execute(
                f'SELECT status, token, created_at FROM {_AUTH_SESSIONS_TABLE} '
                f'WHERE code_challenge = ?', (code_challenge,))
            row = cursor.fetchone()

            if row is None:
                return None

            session = AuthSession(code_challenge, row[0], row[1], row[2])
            if session.is_expired():
                cursor.execute(
                    f'DELETE FROM {_AUTH_SESSIONS_TABLE} '
                    f'WHERE code_challenge = ?', (code_challenge,))
                return None

            return session

    def authorize_session(self, code_challenge: str, token: str) -> bool:
        """Mark a session as authorized and store the token."""
        with self._get_cursor() as cursor:
            self._ensure_table(cursor)

            # Check if session exists and is pending
            cursor.execute(
                f'SELECT status, created_at FROM {_AUTH_SESSIONS_TABLE} '
                f'WHERE code_challenge = ?', (code_challenge,))
            row = cursor.fetchone()

            if row is None:
                return False

            status, created_at = row
            if time.time() - created_at > SESSION_EXPIRATION_SECONDS:
                cursor.execute(
                    f'DELETE FROM {_AUTH_SESSIONS_TABLE} '
                    f'WHERE code_challenge = ?', (code_challenge,))
                return False

            if status != 'pending':
                return False

            # Update session
            cursor.execute(
                f'UPDATE {_AUTH_SESSIONS_TABLE} SET status = ?, token = ? '
                f'WHERE code_challenge = ?',
                ('authorized', token, code_challenge))
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
        code_challenge = common_utils.compute_code_challenge(code_verifier)

        with self._get_cursor() as cursor:
            self._ensure_table(cursor)

            cursor.execute(
                f'SELECT status, token, created_at FROM {_AUTH_SESSIONS_TABLE} '
                f'WHERE code_challenge = ?', (code_challenge,))
            row = cursor.fetchone()

            if row is None:
                return (None, None)

            status, token, created_at = row
            if time.time() - created_at > SESSION_EXPIRATION_SECONDS:
                cursor.execute(
                    f'DELETE FROM {_AUTH_SESSIONS_TABLE} '
                    f'WHERE code_challenge = ?', (code_challenge,))
                return (None, None)

            if status == 'pending':
                return ('pending', None)

            # Authorized - consume and return token
            cursor.execute(
                f'DELETE FROM {_AUTH_SESSIONS_TABLE} WHERE code_challenge = ?',
                (code_challenge,))
            return ('authorized', token)


# Global session store instance
auth_session_store = AuthSessionStore()

"""SQLite-based auth session storage for CLI login flow.

This module provides server-side session storage for the polling-based
CLI authentication flow. Sessions are keyed by code_challenge and
expire after a configurable timeout. Uses SQLite for cross-worker access.
"""
import os
import sqlite3
import time
from typing import Optional

from sky.server import constants as server_constants
from sky.utils import common_utils
from sky.utils.db import db_utils

# Table name for auth sessions
_AUTH_SESSIONS_TABLE = 'auth_sessions'


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
                token TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)

    def _cleanup_expired(self, cursor: sqlite3.Cursor) -> None:
        """Remove expired sessions."""
        expiry_time = time.time(
        ) - server_constants.AUTH_SESSION_TIMEOUT_SECONDS
        cursor.execute(
            f'DELETE FROM {_AUTH_SESSIONS_TABLE} WHERE created_at < ?',
            (expiry_time,))

    def create_session(self, code_challenge: str, token: str) -> None:
        """Create an authorized session with the given token."""
        with self._get_cursor() as cursor:
            self._ensure_table(cursor)
            self._cleanup_expired(cursor)

            # Insert or replace (in case of duplicate authorize clicks)
            cursor.execute(
                f'INSERT OR REPLACE INTO {_AUTH_SESSIONS_TABLE} '
                '(code_challenge, token, created_at) VALUES (?, ?, ?)',
                (code_challenge, token, time.time()))

    def poll_session(self, code_verifier: str) -> Optional[str]:
        """Poll a session for its token using code_verifier.

        Computes code_challenge from code_verifier to look up the session.
        If found and valid, atomically consumes the session and returns the
        token. Uses DELETE ... RETURNING for atomicity (requires SQLite 3.35+,
        which is already required by the API server).

        Returns:
            The token if session exists and is valid, None otherwise.
        """
        code_challenge = common_utils.compute_code_challenge(code_verifier)
        expiry_threshold = time.time(
        ) - server_constants.AUTH_SESSION_TIMEOUT_SECONDS

        with self._get_cursor() as cursor:
            self._ensure_table(cursor)

            # Atomically delete and return token if not expired
            cursor.execute(
                f'DELETE FROM {_AUTH_SESSIONS_TABLE} '
                f'WHERE code_challenge = ? AND created_at > ? '
                f'RETURNING token', (code_challenge, expiry_threshold))
            row = cursor.fetchone()
            return row[0] if row else None


# Global session store instance
auth_session_store = AuthSessionStore()

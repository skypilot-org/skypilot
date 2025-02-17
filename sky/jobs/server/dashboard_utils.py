"""Persistent dashboard sessions.

Note: before #4717, this was useful because we needed to tunnel to multiple
controllers - one per user. Now, there is only one controller for the whole API
server, so this is not very useful. TODO(cooperc): Remove or fix this.
"""
import pathlib
from typing import Tuple

import filelock

from sky.utils import db_utils


def create_dashboard_table(cursor, conn):
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS dashboard_sessions (
        user_hash TEXT PRIMARY KEY,
        port INTEGER,
        pid INTEGER)""")
    conn.commit()


def _get_db_path() -> str:
    path = pathlib.Path('~/.sky/dashboard/sessions.db')
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


DB_PATH = _get_db_path()
db_utils.SQLiteConn(DB_PATH, create_dashboard_table)
LOCK_FILE_PATH = '~/.sky/dashboard/sessions-{user_hash}.lock'


def get_dashboard_session(user_hash: str) -> Tuple[int, int]:
    """Get the port and pid of the dashboard session for the user."""
    with db_utils.safe_cursor(DB_PATH) as cursor:
        cursor.execute(
            'SELECT port, pid FROM dashboard_sessions WHERE user_hash=?',
            (user_hash,))
        result = cursor.fetchone()
        if result is None:
            return 0, 0
        return result


def add_dashboard_session(user_hash: str, port: int, pid: int) -> None:
    """Add a dashboard session for the user."""
    with db_utils.safe_cursor(DB_PATH) as cursor:
        cursor.execute(
            'INSERT OR REPLACE INTO dashboard_sessions (user_hash, port, pid) '
            'VALUES (?, ?, ?)', (user_hash, port, pid))


def remove_dashboard_session(user_hash: str) -> None:
    """Remove the dashboard session for the user."""
    with db_utils.safe_cursor(DB_PATH) as cursor:
        cursor.execute('DELETE FROM dashboard_sessions WHERE user_hash=?',
                       (user_hash,))
    lock_path = pathlib.Path(LOCK_FILE_PATH.format(user_hash=user_hash))
    lock_path.unlink(missing_ok=True)


def get_dashboard_lock_for_user(user_hash: str) -> filelock.FileLock:
    path = pathlib.Path(LOCK_FILE_PATH.format(user_hash=user_hash))
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    return filelock.FileLock(path)

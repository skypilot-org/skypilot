"""Utils for sky databases."""
import contextlib
import sqlite3
import threading
from typing import Any, Callable, Optional

# This parameter (passed to sqlite3.connect) controls how long we will wait to
# obtains a database lock (not necessarily during connection, but whenever it is
# needed). It is not a connection timeout.
# Even in WAL mode, only a single writer is allowed at a time. Other writers
# will block until the write lock can be obtained. This behavior is described in
# the SQLite documentation for WAL: https://www.sqlite.org/wal.html
# Python's default timeout is 5s. In normal usage, lock contention is very low,
# and this is more than sufficient. However, in some highly concurrent cases,
# such as a jobs controller suddenly recovering thousands of jobs at once, we
# can see a small number of processes that take much longer to obtain the lock.
# In contrived highly contentious cases, around 0.1% of transactions will take
# >30s to take the lock. We have not seen cases that take >60s. For cases up to
# 1000x parallelism, this is thus thought to be a conservative setting.
# For more info, see the PR description for #4552.
_DB_TIMEOUT_S = 60


@contextlib.contextmanager
def safe_cursor(db_path: str):
    """A newly created, auto-committing, auto-closing cursor."""
    conn = sqlite3.connect(db_path, timeout=_DB_TIMEOUT_S)
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        conn.commit()
        conn.close()


def add_column_to_table(
    cursor: 'sqlite3.Cursor',
    conn: 'sqlite3.Connection',
    table_name: str,
    column_name: str,
    column_type: str,
    copy_from: Optional[str] = None,
    value_to_replace_existing_entries: Optional[Any] = None,
):
    """Add a column to a table."""
    for row in cursor.execute(f'PRAGMA table_info({table_name})'):
        if row[1] == column_name:
            break
    else:
        try:
            add_column_cmd = (f'ALTER TABLE {table_name} '
                              f'ADD COLUMN {column_name} {column_type}')
            cursor.execute(add_column_cmd)
            if copy_from is not None:
                cursor.execute(f'UPDATE {table_name} '
                               f'SET {column_name} = {copy_from}')
            if value_to_replace_existing_entries is not None:
                cursor.execute(
                    f'UPDATE {table_name} '
                    f'SET {column_name} = (?) '
                    f'WHERE {column_name} IS NULL',
                    (value_to_replace_existing_entries,))
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e):
                # We may be trying to add the same column twice, when
                # running multiple threads. This is fine.
                pass
            else:
                raise
    conn.commit()


def rename_column(
    cursor: 'sqlite3.Cursor',
    conn: 'sqlite3.Connection',
    table_name: str,
    old_name: str,
    new_name: str,
):
    """Rename a column in a table."""
    # NOTE: This only works for sqlite3 >= 3.25.0. Be careful to use this.

    for row in cursor.execute(f'PRAGMA table_info({table_name})'):
        if row[1] == old_name:
            cursor.execute(f'ALTER TABLE {table_name} '
                           f'RENAME COLUMN {old_name} to {new_name}')
            break
    conn.commit()


class SQLiteConn(threading.local):
    """Thread-local connection to the sqlite3 database."""

    def __init__(self, db_path: str, create_table: Callable):
        super().__init__()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=_DB_TIMEOUT_S)
        self.cursor = self.conn.cursor()
        create_table(self.cursor, self.conn)

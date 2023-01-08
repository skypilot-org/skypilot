"""Skylet configs."""
import contextlib
import functools
import os
import pathlib
import sqlite3
from typing import Optional

_DB_PATH = os.path.expanduser('~/.sky/skylet_config.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)


@contextlib.contextmanager
def _safe_cursor():
    """A newly created, auto-commiting, auto-closing cursor."""
    conn = sqlite3.connect(_DB_PATH)
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        conn.commit()
        conn.close()

def ensure_table(func: callable):
    """Ensure the table exists before calling the function."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _safe_cursor() as c:  # Call it 'c' to avoid pylint complaining.
            # Use WAL mode to avoid locking problem in #1507.
            # Reference: https://stackoverflow.com/a/39265148
            c.execute('PRAGMA journal_mode=WAL')
            c.execute("""\
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT)""")
        return func(*args, **kwargs)
    return wrapper

@ensure_table
def get_config(key: str) -> Optional[str]:
    with _safe_cursor() as cursor:
        rows = cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
        for (value,) in rows:
            return value

@ensure_table
def set_config(key: str, value: str) -> None:
    with _safe_cursor() as cursor:
        cursor.execute(
            """\
            INSERT OR REPLACE INTO config VALUES (?, ?)
            """, (key, value))

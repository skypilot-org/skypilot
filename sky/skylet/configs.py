"""Skylet configs."""
import contextlib
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
    # Use WAL mode to avoid locking problem in #1507.
    # Reference: https://stackoverflow.com/a/39265148
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        conn.commit()
        conn.close()


with _safe_cursor() as c:  # Call it 'c' to avoid pylint complaining.
    c.execute("""\
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT)""")


def get_config(key: str) -> Optional[str]:
    with _safe_cursor() as cursor:
        rows = cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
        for (value,) in rows:
            return value


def set_config(key: str, value: str) -> None:
    with _safe_cursor() as cursor:
        cursor.execute(
            """\
            INSERT OR REPLACE INTO config VALUES (?, ?)
            """, (key, value))

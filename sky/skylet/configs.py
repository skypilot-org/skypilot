"""skylet configs"""
import os
import pathlib
import sqlite3
from typing import Optional

_DB_PATH = os.path.expanduser('~/.sky/skylet_config.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_CONN = sqlite3.connect(_DB_PATH)
_CURSOR = _CONN.cursor()

_CURSOR.execute("""\
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, 
        value TEXT)""")

_CONN.commit()


def get_config(key: str) -> Optional[str]:
    rows = _CURSOR.execute('SELECT value FROM config WHERE key = ?', (key,))
    for (value,) in rows:
        return value


def set_config(key: str, value: str) -> None:
    _CURSOR.execute(
        """\
        INSERT OR REPLACE INTO config VALUES (?, ?)
        """, (key, value))
    _CONN.commit()

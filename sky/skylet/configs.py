"""Skylet configs."""
import functools
import os
import pathlib
from typing import Callable, Optional, Union

from sky.utils import db_utils

_DB_PATH = os.path.expanduser('~/.sky/skylet_config.db')
os.makedirs(pathlib.Path(_DB_PATH).parents[0], exist_ok=True)

_table_created = False


def ensure_table(func: Callable):
    """Ensure the table exists before calling the function.

    Since this module will be imported whenever `sky` is imported (due to
    Python's package importing logic), we should avoid creating the table
    until it's actually needed to avoid too many concurrent commit to the
    database.
    It solves the database locked problem in #1576.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _table_created
        if not _table_created:
            with db_utils.safe_cursor(
                    _DB_PATH) as c:  # Call it 'c' to avoid pylint complaining.
                # Use WAL mode to avoid locking problem in #1507.
                # Reference: https://stackoverflow.com/a/39265148
                c.execute('PRAGMA journal_mode=WAL')
                c.execute("""\
                    CREATE TABLE IF NOT EXISTS config (
                        key TEXT PRIMARY KEY,
                        value TEXT)""")
        _table_created = True
        return func(*args, **kwargs)

    return wrapper


@ensure_table
def get_config(key: str) -> Optional[bytes]:
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        rows = cursor.execute('SELECT value FROM config WHERE key = ?', (key,))
        for (value,) in rows:
            return value
        return None


@ensure_table
def set_config(key: str, value: Union[bytes, str]) -> None:
    with db_utils.safe_cursor(_DB_PATH) as cursor:
        cursor.execute(
            """\
            INSERT OR REPLACE INTO config VALUES (?, ?)
            """, (key, value))
